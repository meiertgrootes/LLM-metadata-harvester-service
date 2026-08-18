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
from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from llm_metadata_harvester_service.core.celery_app import celery_app
from llm_metadata_harvester_service.core.config import (
    JOB_PENDING_STALE_SECONDS,
    JOB_QUEUED_STALE_SECONDS,
)
from llm_metadata_harvester_service.db.models import Job, WebhookOutbox
from llm_metadata_harvester_service.db.session import SessionLocal
from llm_metadata_harvester_service.db.status import TERMINAL_JOB_STATUSES, JobStatus

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


def _ensure_webhook_outbox(
    db: Session, job: Job, *, event: str, now: datetime
) -> None:
    if job.webhook_url is None:
        job.webhook_secret_encrypted = None
        return
    if db.get(WebhookOutbox, job.job_id) is None:
        db.add(
            WebhookOutbox(
                job_id=job.job_id,
                event=event,
                encrypted_secret=job.webhook_secret_encrypted,
                next_attempt_at=now,
            )
        )
    job.webhook_secret_encrypted = None


def _claim_job(job_id: str) -> tuple[int | None, dict[str, Any]]:
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
            }
        if job.status in TERMINAL_JOB_STATUSES or job.status == JobStatus.PENDING:
            return None, _payload_from_job(job)

        job.execution_attempts += 1
        job.status = JobStatus.PENDING
        job.error = None
        job.completed_at = None
        return job.execution_attempts, _payload_from_job(job)


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
        if outcome.rowcount != 1:
            return False
        job = db.get(Job, job_id)
        if job is None:
            raise RuntimeError(f"Job disappeared while completing: {job_id}")
        event = "job.completed" if status == JobStatus.SUCCESS else "job.failed"
        _ensure_webhook_outbox(db, job, event=event, now=now)
        return True


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
    api_key: str,
) -> dict[str, Any]:
    attempt, existing_payload = _claim_job(job_id)
    if attempt is None:
        return existing_payload

    stdout_buffer = io.StringIO()
    result: dict[str, Any]
    try:
        with redirect_stdout(stdout_buffer):
            result = asyncio.run(
                metadata_harvest(model_name=model, url=url, api_key=api_key)
            )
    except SoftTimeLimitExceeded:
        error_logs = stdout_buffer.getvalue() + "\n" + traceback.format_exc()
        _complete_job(
            job_id,
            attempt,
            status=JobStatus.FAILURE,
            result=None,
            logs=error_logs,
            error="harvest_time_limit_exceeded",
        )
        raise
    except Exception:
        error_logs = stdout_buffer.getvalue() + "\n" + traceback.format_exc()
        won = _complete_job(
            job_id,
            attempt,
            status=JobStatus.FAILURE,
            result=None,
            logs=error_logs,
            error="harvest_failed",
        )
        if not won:
            return _current_payload(job_id)
        payload = _current_payload(job_id)
        sys.stderr.write(json.dumps(payload) + "\n")
        sys.stderr.flush()
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

    payload = _current_payload(job_id)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return payload


@celery_app.task(bind=True)
def run_harvester_task(
    self: Any,
    *,
    model: str,
    url: str,
    api_key: str,
) -> dict[str, Any]:
    return _run_harvest(
        self.request.id,
        model=model,
        url=url,
        api_key=api_key,
    )


@celery_app.task
def reconcile_stale_jobs() -> int:
    now = datetime.now(UTC)
    pending_cutoff = now - timedelta(seconds=JOB_PENDING_STALE_SECONDS)
    queued_cutoff = now - timedelta(seconds=JOB_QUEUED_STALE_SECONDS)
    with SessionLocal.begin() as db:
        jobs = list(
            db.scalars(
                select(Job)
                .where(
                    or_(
                        (Job.status == JobStatus.PENDING)
                        & (Job.updated_at < pending_cutoff),
                        (Job.status == JobStatus.QUEUED)
                        & (Job.updated_at < queued_cutoff),
                    )
                )
                .with_for_update(skip_locked=True)
            )
        )
        for job in jobs:
            job.error = (
                "worker_lost"
                if job.status == JobStatus.PENDING
                else "dispatch_lost"
            )
            job.status = JobStatus.FAILURE
            job.completed_at = now
            job.updated_at = now
            _ensure_webhook_outbox(db, job, event="job.failed", now=now)
    return len(jobs)
