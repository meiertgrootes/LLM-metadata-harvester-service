import hashlib
import hmac
import json
import logging
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import httpcore
import httpx
from sqlalchemy import or_, select, update

from llm_metadata_harvester_service.core.celery_app import celery_app
from llm_metadata_harvester_service.core.config import (
    WEBHOOK_MAX_ATTEMPTS,
    WEBHOOK_OUTBOX_LEASE_SECONDS,
    WEBHOOK_RETRY_AFTER_MAX_SECONDS,
    WEBHOOK_RETRY_BACKOFF,
    WEBHOOK_TIMEOUT_SECONDS,
)
from llm_metadata_harvester_service.core.secrets import decrypt_webhook_secret
from llm_metadata_harvester_service.core.webhook_security import validate_webhook_url
from llm_metadata_harvester_service.db.models import Job, WebhookOutbox
from llm_metadata_harvester_service.db.session import SessionLocal

logger = logging.getLogger(__name__)
_WEBHOOK_MAX_BACKOFF_SECONDS = 32


@dataclass(frozen=True)
class DeliveryOutcome:
    delivered: bool
    error: str | None = None
    retry_after: float | None = None


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
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _build_body(event: str, payload: dict[str, Any]) -> bytes:
    body = {
        "event": event,
        "timestamp": datetime.now(UTC).isoformat(),
        **payload,
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _parse_retry_after(value: str) -> float | None:
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            seconds = (retry_at - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return min(max(seconds, 0), WEBHOOK_RETRY_AFTER_MAX_SECONDS)


def _deliver_once(
    *,
    payload: dict[str, Any],
    webhook_url: str,
    webhook_secret: str | None,
    event: str,
) -> DeliveryOutcome:
    addresses = validate_webhook_url(webhook_url)
    body = _build_body(event, payload)
    headers = {"Content-Type": "application/json"}
    if webhook_secret:
        headers["X-Webhook-Signature"] = f"sha256={sign_payload(body, webhook_secret)}"

    transport = (
        _PinnedHTTPTransport(urlparse(webhook_url).hostname or "", addresses)
        if addresses is not None
        else None
    )
    try:
        with httpx.Client(
            timeout=WEBHOOK_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            response = client.post(webhook_url, content=body, headers=headers)
    except httpx.HTTPError:
        return DeliveryOutcome(False, "connection_error")

    if not 200 <= response.status_code < 300:
        retry_after = response.headers.get("Retry-After")
        return DeliveryOutcome(
            False,
            f"http_status:{response.status_code}",
            _parse_retry_after(retry_after) if retry_after else None,
        )
    return DeliveryOutcome(True)


def _job_payload(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "status": job.status,
        "model": job.model,
        "result": job.result,
        "logs": job.logs or "",
        "error": job.error,
    }


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


@celery_app.task
def publish_pending_webhooks(limit: int = 100) -> int:
    now = datetime.now(UTC)
    lease_cutoff = now - timedelta(seconds=WEBHOOK_OUTBOX_LEASE_SECONDS)
    with SessionLocal.begin() as db:
        rows = list(
            db.scalars(
                select(WebhookOutbox)
                .where(
                    WebhookOutbox.delivered_at.is_(None),
                    WebhookOutbox.exhausted_at.is_(None),
                    WebhookOutbox.next_attempt_at <= now,
                    or_(
                        WebhookOutbox.published_at.is_(None),
                        WebhookOutbox.published_at < lease_cutoff,
                    ),
                    or_(
                        WebhookOutbox.delivery_started_at.is_(None),
                        WebhookOutbox.delivery_started_at < lease_cutoff,
                    ),
                )
                .order_by(WebhookOutbox.next_attempt_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for row in rows:
            row.published_at = now
        dispatches = [(row.job_id, row.attempts + 1) for row in rows]

    published = 0
    for job_id, attempt in dispatches:
        try:
            deliver_job_webhook.apply_async(
                kwargs={"job_id": job_id},
                task_id=f"webhook:{job_id}:{attempt}",
            )
            published += 1
        except Exception:
            logger.exception("failed to publish webhook outbox job %s", job_id)
            with SessionLocal.begin() as db:
                db.execute(
                    update(WebhookOutbox)
                    .where(
                        WebhookOutbox.job_id == job_id,
                        WebhookOutbox.published_at == now,
                    )
                    .values(published_at=None, last_error="publish_failed")
                )
    return published


@celery_app.task
def deliver_job_webhook(*, job_id: str) -> str:
    started_at = datetime.now(UTC)
    lease_cutoff = started_at - timedelta(seconds=WEBHOOK_OUTBOX_LEASE_SECONDS)
    with SessionLocal.begin() as db:
        outbox = db.scalar(
            select(WebhookOutbox)
            .where(WebhookOutbox.job_id == job_id)
            .with_for_update()
        )
        if outbox is None:
            return "missing"
        if outbox.delivered_at is not None:
            return "delivered"
        if outbox.exhausted_at is not None:
            return "exhausted"
        if _utc(outbox.next_attempt_at) > started_at:
            return "not_due"
        if (
            outbox.delivery_started_at is not None
            and _utc(outbox.delivery_started_at) >= lease_cutoff
        ):
            return "in_progress"

        job = db.get(Job, job_id)
        if job is None or job.webhook_url is None:
            outbox.exhausted_at = started_at
            outbox.last_error = "job_or_webhook_missing"
            outbox.encrypted_secret = None
            return "exhausted"

        outbox.attempts += 1
        attempt = outbox.attempts
        outbox.delivery_started_at = started_at
        job.webhook_attempts = attempt
        job.webhook_last_attempt_at = started_at
        payload = _job_payload(job)
        webhook_url = job.webhook_url
        event = outbox.event
        encrypted_secret = outbox.encrypted_secret

    try:
        secret = (
            decrypt_webhook_secret(encrypted_secret)
            if encrypted_secret is not None
            else None
        )
        outcome = _deliver_once(
            payload=payload,
            webhook_url=webhook_url,
            webhook_secret=secret,
            event=event,
        )
    except Exception as exc:
        outcome = DeliveryOutcome(False, f"delivery_error:{type(exc).__name__}")

    finished_at = datetime.now(UTC)
    with SessionLocal.begin() as db:
        outbox = db.scalar(
            select(WebhookOutbox)
            .where(WebhookOutbox.job_id == job_id)
            .with_for_update()
        )
        if outbox is None:
            return "missing"
        if outbox.attempts != attempt:
            return "superseded"
        job = db.get(Job, job_id)
        if outcome.delivered:
            outbox.delivered_at = finished_at
            outbox.encrypted_secret = None
            outbox.last_error = None
            if job is not None:
                job.webhook_delivered_at = finished_at
                job.webhook_error = None
            return "delivered"

        outbox.last_error = outcome.error or "delivery_failed"
        if job is not None:
            job.webhook_error = outbox.last_error
        if attempt >= WEBHOOK_MAX_ATTEMPTS:
            outbox.exhausted_at = finished_at
            outbox.encrypted_secret = None
            return "exhausted"

        delay = outcome.retry_after
        if delay is None:
            delay = min(
                WEBHOOK_RETRY_BACKOFF * (2 ** (attempt - 1)),
                _WEBHOOK_MAX_BACKOFF_SECONDS,
            )
        outbox.next_attempt_at = finished_at + timedelta(seconds=delay)
        outbox.published_at = None
        outbox.delivery_started_at = None
        return "retry_scheduled"
