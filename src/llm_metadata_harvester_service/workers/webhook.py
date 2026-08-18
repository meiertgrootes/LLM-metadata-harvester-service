# src/llm_metadata_harvester_service/workers/webhook.py

import hashlib
import hmac
import json
import logging
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import httpcore
import httpx
from celery.exceptions import Retry
from sqlalchemy import case, update

from llm_metadata_harvester_service.core.celery_app import celery_app
from llm_metadata_harvester_service.core.config import (
    WEBHOOK_MAX_ATTEMPTS,
    WEBHOOK_RETRY_AFTER_MAX_SECONDS,
    WEBHOOK_RETRY_BACKOFF,
    WEBHOOK_TIMEOUT_SECONDS,
)
from llm_metadata_harvester_service.core.secrets import decrypt_task_secret
from llm_metadata_harvester_service.core.webhook_security import validate_webhook_url
from llm_metadata_harvester_service.db.models import Job
from llm_metadata_harvester_service.db.session import SessionLocal

logger = logging.getLogger(__name__)

_WEBHOOK_MAX_BACKOFF_SECONDS = 32


@dataclass(frozen=True)
class DeliveryOutcome:
    delivered: bool
    error: str | None = None


class _PinnedNetworkBackend(httpcore.SyncBackend):
    def __init__(self, host: str, addresses: tuple[str, ...]) -> None:
        self.host = host.lower().rstrip(".")
        self.addresses = addresses

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.NetworkStream:
        if host.lower().rstrip(".") != self.host:
            raise OSError("Unexpected webhook connection host")
        last_error: Exception | None = None
        for address in self.addresses:
            try:
                return super().connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:
                last_error = exc
        raise OSError("Could not connect to a validated webhook address") from last_error


class _PinnedHTTPTransport(httpx.HTTPTransport):
    def __init__(self, host: str, addresses: tuple[str, ...]) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore.ConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=_PinnedNetworkBackend(host, addresses),
        )


def sign_payload(body: bytes, secret: str) -> str:
    """Return the HMAC-SHA256 signature of the raw webhook body."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _build_body(event: str, payload: dict[str, Any]) -> bytes:
    body = {
        "event": event,
        "timestamp": datetime.now(UTC).isoformat(),
        **payload,
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _record_attempt(job_id: str) -> None:
    with SessionLocal.begin() as db:
        db.execute(
            update(Job)
            .where(Job.job_id == job_id)
            .values(
                webhook_attempts=Job.webhook_attempts + 1,
                webhook_last_attempt_at=datetime.now(UTC),
            )
        )


def _record_delivered(job_id: str) -> None:
    with SessionLocal.begin() as db:
        db.execute(
            update(Job)
            .where(Job.job_id == job_id)
            .values(webhook_delivered_at=datetime.now(UTC), webhook_error=None)
        )


def _record_failed(job_id: str, error: str) -> None:
    with SessionLocal.begin() as db:
        db.execute(
            update(Job)
            .where(Job.job_id == job_id)
            .values(
                webhook_error=case(
                    (Job.webhook_delivered_at.is_(None), error),
                    else_=Job.webhook_error,
                )
            )
        )


@celery_app.task(
    bind=True,
    max_retries=WEBHOOK_MAX_ATTEMPTS - 1,
    default_retry_delay=WEBHOOK_RETRY_BACKOFF,
)
def dispatch_webhook(
    self: Any,
    *,
    payload: dict[str, Any],
    webhook_url: str,
    encrypted_webhook_secret: str | None,
    event: str,
) -> None:
    """
    Deliver a job payload to a client-supplied webhook endpoint.

    The body is HMAC-SHA256 signed when a ``webhook_secret`` was provided at
    submission time. Delivery is retried with exponential backoff on transient
    HTTP errors and non-2xx responses, honouring ``Retry-After`` when present.
    Delivery metadata is recorded on the job row (best-effort; database
    failures never block delivery).
    """
    job_id = payload["job_id"]
    webhook_secret = (
        decrypt_task_secret(encrypted_webhook_secret)
        if encrypted_webhook_secret is not None
        else None
    )

    try:
        _record_attempt(job_id)
    except Exception:
        logger.warning("could not record webhook attempt for %s", job_id, exc_info=True)

    try:
        outcome = _deliver(
            self,
            payload=payload,
            webhook_url=webhook_url,
            webhook_secret=webhook_secret,
            event=event,
        )
    except Retry:
        raise
    except Exception as exc:
        try:
            _record_failed(job_id, str(exc))
        except Exception:
            logger.warning("could not record webhook failure for %s", job_id, exc_info=True)
        raise

    try:
        if outcome.delivered:
            _record_delivered(job_id)
        else:
            _record_failed(job_id, outcome.error or "delivery_failed")
    except Exception:
        logger.warning("could not record webhook outcome for %s", job_id, exc_info=True)


def _deliver(
    self: Any,
    *,
    payload: dict[str, Any],
    webhook_url: str,
    webhook_secret: str | None,
    event: str,
) -> DeliveryOutcome:
    """Attempt delivery and return the final outcome or schedule a retry."""
    scheme = urlparse(webhook_url).scheme
    if scheme not in ("http", "https"):
        raise ValueError(f"Unsupported webhook URL scheme: {scheme!r}")
    addresses = validate_webhook_url(webhook_url)

    body = _build_body(event, payload)

    headers = {"Content-Type": "application/json"}
    if webhook_secret:
        headers["X-Webhook-Signature"] = f"sha256={sign_payload(body, webhook_secret)}"

    is_final_attempt = self.request.retries >= self.max_retries
    countdown = min(
        WEBHOOK_RETRY_BACKOFF * (2**self.request.retries),
        _WEBHOOK_MAX_BACKOFF_SECONDS,
    )

    try:
        transport = (
            _PinnedHTTPTransport(urlparse(webhook_url).hostname or "", addresses)
            if addresses is not None
            else None
        )
        with httpx.Client(
            timeout=WEBHOOK_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            response = client.post(webhook_url, content=body, headers=headers)
    except httpx.HTTPError as exc:
        if is_final_attempt:
            logger.error(
                "webhook delivery failed permanently for %s (event=%s): %s",
                webhook_url,
                event,
                exc,
            )
            return DeliveryOutcome(False, "connection_error")
        raise self.retry(exc=exc, countdown=countdown) from exc

    if not 200 <= response.status_code < 300:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            countdown = _parse_retry_after(retry_after, countdown)

        if is_final_attempt:
            logger.error(
                "webhook delivery failed permanently for %s (event=%s): HTTP %s",
                webhook_url,
                event,
                response.status_code,
            )
            return DeliveryOutcome(False, f"http_status:{response.status_code}")

        logger.warning(
            "webhook delivery failed (attempt %s/%s) for %s: HTTP %s",
            self.request.retries + 1,
            WEBHOOK_MAX_ATTEMPTS,
            webhook_url,
            response.status_code,
        )
        raise self.retry(
            exc=httpx.HTTPStatusError(
                f"Webhook endpoint returned HTTP {response.status_code}",
                request=response.request,
                response=response,
            ),
            countdown=countdown,
        )

    logger.info("webhook delivered to %s (event=%s)", webhook_url, event)
    return DeliveryOutcome(True)


def _parse_retry_after(value: str, fallback: float) -> float:
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            seconds = (retry_at - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return fallback
    return min(max(seconds, 0), WEBHOOK_RETRY_AFTER_MAX_SECONDS)
