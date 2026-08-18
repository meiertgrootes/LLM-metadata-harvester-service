import asyncio
import io
import json
import logging
import sys
import traceback
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from typing import Any

from billiard.exceptions import SoftTimeLimitExceeded
from llm_metadata_harvester.harvester_operations import metadata_harvest
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult

from llm_metadata_harvester_service.core.celery_app import celery_app
from llm_metadata_harvester_service.core.config import (
    JOB_MAX_EXECUTION_ATTEMPTS,
    JOB_PENDING_STALE_SECONDS,
    JOB_QUEUED_STALE_SECONDS,
)
from llm_metadata_harvester_service.core.secrets import decrypt_task_secret
from llm_metadata_harvester_service.db.models import Job
from llm_metadata_harvester_service.db.session import SessionLocal
from llm_metadata_harvester_service.db.status import TERMINAL_JOB_STATUSES, JobStatus
from llm_metadata_harvester_service.workers.webhook import dispatch_webhook

logger = logging.getLogger(__name__)


def _payload_from_job(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "status": job.status,
        "model": job.model,
        "result": job.result,
        "logs": job.logs or "",
        "error": job.error,
    }


def _claim_job(
    job_id: str, *, allow_pending_reclaim: bool
) -> tuple[int | None, dict[str, Any], bool]:
    with SessionLocal.begin() as db:
        job = db.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
        if job is None:
            logger.error("received task for unknown job %s", job_id)
            return None, {
                "job_id": job_id,
                "status": JobStatus.FAILURE,
                "model": "",
                "result": None,
                "logs": "",
                "error": "job_not_found",
            }, False
        if job.status in TERMINAL_JOB_STATUSES:
            return None, _payload_from_job(job), False
        if job.status == JobStatus.PENDING and not allow_pending_reclaim:
            return None, _payload_from_job(job), False
        if job.execution_attempts >= JOB_MAX_EXECUTION_ATTEMPTS:
            job.status = JobStatus.FAILURE
            job.error = "execution_attempts_exhausted"
            job.completed_at = datetime.now(UTC)
            return None, _payload_from_job(job), True

        job.execution_attempts += 1
        job.status = JobStatus.PENDING
        job.error = None
        job.completed_at = None
        return job.execution_attempts, _payload_from_job(job), False


def _complete_job(
    job_id: str,
    attempt: int,
    *,
    status: JobStatus,
    result: dict[str, Any] | None,
    logs: str,
    error: str | None,
) -> bool:
    now = datetime.now(UTC)
    with SessionLocal.begin() as db:
        outcome = db.execute(
            update(Job)
            .where(
                Job.job_id == job_id,
                Job.status == JobStatus.PENDING,
                Job.execution_attempts == attempt,
            )
            .values(
                status=status,
                result=result,
                logs=logs,
                error=error,
                completed_at=now,
                updated_at=now,
            )
        )
        assert isinstance(outcome, CursorResult)
        return outcome.rowcount == 1


def _record_webhook_enqueue_failure(job_id: str) -> None:
    with SessionLocal.begin() as db:
        db.execute(
            update(Job)
            .where(Job.job_id == job_id)
            .values(webhook_error="dispatch_failed", updated_at=datetime.now(UTC))
        )


def _enqueue_webhook(
    *,
    payload: dict[str, Any],
    webhook_url: str | None,
    encrypted_webhook_secret: str | None,
    event: str,
) -> None:
    if webhook_url is None:
        return
    try:
        dispatch_webhook.apply_async(
            kwargs={
                "payload": payload,
                "webhook_url": webhook_url,
                "encrypted_webhook_secret": encrypted_webhook_secret,
                "event": event,
            },
            task_id=f"webhook:{payload['job_id']}:{event}",
        )
    except Exception:
        logger.exception("failed to enqueue webhook for job %s", payload["job_id"])
        try:
            _record_webhook_enqueue_failure(payload["job_id"])
        except Exception:
            logger.exception(
                "failed to record webhook enqueue failure for job %s",
                payload["job_id"],
            )


def _current_payload(job_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            raise RuntimeError(f"Job disappeared while running: {job_id}")
        return _payload_from_job(job)


def _run_harvest(
    job_id: str,
    *,
    model: str,
    url: str,
    encrypted_api_key: str,
    webhook_url: str | None = None,
    encrypted_webhook_secret: str | None = None,
    allow_pending_reclaim: bool = False,
) -> dict[str, Any]:
    attempt, existing_payload, notify = _claim_job(
        job_id, allow_pending_reclaim=allow_pending_reclaim
    )
    if attempt is None:
        if notify:
            _enqueue_webhook(
                payload=existing_payload,
                webhook_url=webhook_url,
                encrypted_webhook_secret=encrypted_webhook_secret,
                event="job.failed",
            )
        return existing_payload

    api_key = decrypt_task_secret(encrypted_api_key)

    stdout_buffer = io.StringIO()
    result: dict[str, Any]
    try:
        with redirect_stdout(stdout_buffer):
            result = asyncio.run(
                metadata_harvest(model_name=model, url=url, api_key=api_key)
            )
    except SoftTimeLimitExceeded:
        error_code = "harvest_time_limit_exceeded"
        error_logs = stdout_buffer.getvalue() + "\n" + traceback.format_exc()
        won = _complete_job(
            job_id,
            attempt,
            status=JobStatus.FAILURE,
            result=None,
            logs=error_logs,
            error=error_code,
        )
        if won:
            payload: dict[str, Any] = {
                "job_id": job_id,
                "status": JobStatus.FAILURE,
                "model": model,
                "result": None,
                "logs": error_logs,
                "error": error_code,
            }
            _enqueue_webhook(
                payload=payload,
                webhook_url=webhook_url,
                encrypted_webhook_secret=encrypted_webhook_secret,
                event="job.failed",
            )
        raise
    except Exception:
        error_code = "harvest_failed"
        error_logs = stdout_buffer.getvalue() + "\n" + traceback.format_exc()
        won = _complete_job(
            job_id,
            attempt,
            status=JobStatus.FAILURE,
            result=None,
            logs=error_logs,
            error=error_code,
        )
        if not won:
            return _current_payload(job_id)
        payload = {
            "job_id": job_id,
            "status": JobStatus.FAILURE,
            "model": model,
            "result": None,
            "logs": error_logs,
            "error": error_code,
        }
        sys.stderr.write(json.dumps(payload) + "\n")
        sys.stderr.flush()
        _enqueue_webhook(
            payload=payload,
            webhook_url=webhook_url,
            encrypted_webhook_secret=encrypted_webhook_secret,
            event="job.failed",
        )
        raise

    logs = stdout_buffer.getvalue()
    won = _complete_job(
        job_id,
        attempt,
        status=JobStatus.SUCCESS,
        result=result,
        logs=logs,
        error=None,
    )
    if not won:
        return _current_payload(job_id)

    payload = {
        "job_id": job_id,
        "status": JobStatus.SUCCESS,
        "model": model,
        "result": result,
        "logs": logs,
        "error": None,
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    _enqueue_webhook(
        payload=payload,
        webhook_url=webhook_url,
        encrypted_webhook_secret=encrypted_webhook_secret,
        event="job.completed",
    )
    return payload


@celery_app.task(bind=True)
def run_harvester_task(
    self: Any,
    *,
    model: str,
    url: str,
    encrypted_api_key: str,
    webhook_url: str | None = None,
    encrypted_webhook_secret: str | None = None,
) -> dict[str, Any]:
    return _run_harvest(
        self.request.id,
        model=model,
        url=url,
        encrypted_api_key=encrypted_api_key,
        webhook_url=webhook_url,
        encrypted_webhook_secret=encrypted_webhook_secret,
        allow_pending_reclaim=bool(
            (self.request.delivery_info or {}).get("redelivered", False)
        ),
    )


@celery_app.task
def reconcile_stale_jobs() -> int:
    now = datetime.now(UTC)
    pending_cutoff = now - timedelta(seconds=JOB_PENDING_STALE_SECONDS)
    queued_cutoff = now - timedelta(seconds=JOB_QUEUED_STALE_SECONDS)
    with SessionLocal.begin() as db:
        pending_result = db.execute(
            update(Job)
            .where(Job.status == JobStatus.PENDING, Job.updated_at < pending_cutoff)
            .values(
                status=JobStatus.FAILURE,
                error="worker_lost",
                completed_at=now,
                updated_at=now,
            )
        )
        queued_result = db.execute(
            update(Job)
            .where(Job.status == JobStatus.QUEUED, Job.updated_at < queued_cutoff)
            .values(
                status=JobStatus.FAILURE,
                error="dispatch_lost",
                completed_at=now,
                updated_at=now,
            )
        )
        assert isinstance(pending_result, CursorResult)
        assert isinstance(queued_result, CursorResult)
        pending = pending_result.rowcount
        queued = queued_result.rowcount
    return int(pending + queued)
