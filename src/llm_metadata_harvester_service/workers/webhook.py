# src/llm_metadata_harvester_service/workers/webhook.py

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from celery import shared_task
from celery.exceptions import Retry

from llm_metadata_harvester_service.core.config import (
    WEBHOOK_MAX_RETRIES,
    WEBHOOK_RETRY_BACKOFF,
    WEBHOOK_TIMEOUT_SECONDS,
)
from llm_metadata_harvester_service.db.models import Job
from llm_metadata_harvester_service.db.session import SessionLocal

logger = logging.getLogger(__name__)

_WEBHOOK_MAX_BACKOFF_SECONDS = 32


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
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.webhook_attempts = (job.webhook_attempts or 0) + 1
        job.webhook_last_attempt_at = datetime.now(UTC)
        db.commit()


def _record_delivered(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.webhook_delivered_at = datetime.now(UTC)
        job.webhook_error = None
        db.commit()


def _record_failed(job_id: str, error: str) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.webhook_error = error
        db.commit()


@shared_task(
    bind=True,
    max_retries=WEBHOOK_MAX_RETRIES,
    default_retry_delay=WEBHOOK_RETRY_BACKOFF,
    retry_backoff=WEBHOOK_RETRY_BACKOFF,
    retry_backoff_max=_WEBHOOK_MAX_BACKOFF_SECONDS,
    retry_jitter=False,
)
def dispatch_webhook(
    self: Any,
    *,
    payload: dict[str, Any],
    webhook_url: str,
    webhook_secret: str | None,
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

    try:
        _record_attempt(job_id)
    except Exception:
        logger.warning("could not record webhook attempt for %s", job_id, exc_info=True)

    try:
        delivered = _deliver(
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
        if delivered:
            _record_delivered(job_id)
        else:
            _record_failed(job_id, "delivery failed permanently")
    except Exception:
        logger.warning("could not record webhook outcome for %s", job_id, exc_info=True)


def _deliver(
    self: Any,
    *,
    payload: dict[str, Any],
    webhook_url: str,
    webhook_secret: str | None,
    event: str,
) -> bool:
    """Attempt one webhook delivery. Returns True when delivered, False when the
    final attempt failed; raises ``Retry`` to schedule another attempt."""
    scheme = urlparse(webhook_url).scheme
    if scheme not in ("http", "https"):
        raise ValueError(f"Unsupported webhook URL scheme: {scheme!r}")

    body = _build_body(event, payload)

    headers = {"Content-Type": "application/json"}
    if webhook_secret:
        headers["X-Webhook-Signature"] = f"sha256={sign_payload(body, webhook_secret)}"

    is_final_attempt = self.request.retries >= self.max_retries

    try:
        with httpx.Client(timeout=WEBHOOK_TIMEOUT_SECONDS) as client:
            response = client.post(webhook_url, content=body, headers=headers)
    except httpx.HTTPError as exc:
        if is_final_attempt:
            logger.error(
                "webhook delivery failed permanently for %s (event=%s): %s",
                webhook_url,
                event,
                exc,
            )
            return False
        raise self.retry(exc=exc) from exc

    if response.status_code >= 400:
        retry_after = response.headers.get("Retry-After")
        countdown = None
        if retry_after:
            try:
                countdown = float(retry_after)
            except ValueError:
                countdown = None

        if is_final_attempt:
            logger.error(
                "webhook delivery failed permanently for %s (event=%s): HTTP %s",
                webhook_url,
                event,
                response.status_code,
            )
            return False

        logger.warning(
            "webhook delivery failed (attempt %s/%s) for %s: HTTP %s",
            self.request.retries + 1,
            WEBHOOK_MAX_RETRIES,
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
    return True
