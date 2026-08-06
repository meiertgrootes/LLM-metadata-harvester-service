# src/llm_metadata_harvester_service/workers/tasks.py

import asyncio
import io
import json
import sys
import traceback
from contextlib import redirect_stdout
from datetime import UTC, datetime
from typing import Any

from celery import shared_task
from llm_metadata_harvester.harvester_operations import metadata_harvest

from llm_metadata_harvester_service.db.models import Job
from llm_metadata_harvester_service.db.session import SessionLocal
from llm_metadata_harvester_service.workers.webhook import dispatch_webhook


def _update_job(
    job_id: str,
    *,
    url: str | None = None,
    model: str | None = None,
    **fields: Any,
) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            job = Job(
                job_id=job_id,
                url=url or "",
                model=model or "",
                status=fields.get("status", "pending"),
            )
            db.add(job)
        for key, value in fields.items():
            setattr(job, key, value)
        db.commit()


def _run_harvest(
    job_id: str,
    *,
    model: str,
    url: str,
    api_key: str,
    webhook_url: str | None = None,
    webhook_secret: str | None = None,
) -> dict[str, Any]:
    _update_job(job_id, url=url, model=model, status="pending")

    stdout_buffer = io.StringIO()

    try:
        with redirect_stdout(stdout_buffer):
            result = asyncio.run(
                metadata_harvest(
                    model_name=model,
                    url=url,
                    api_key=api_key,
                )
            )

        logs = stdout_buffer.getvalue()

        _update_job(
            job_id,
            status="success",
            result=result,
            logs=logs,
            completed_at=datetime.now(UTC),
        )

        payload = {
            "job_id": job_id,
            "status": "success",
            "model": model,
            "result": result,
            "logs": logs,
            "error": None,
        }

        # Operator visibility (optional)
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()

        if webhook_url:
            dispatch_webhook.delay(
                payload=payload,
                webhook_url=webhook_url,
                webhook_secret=webhook_secret,
                event="job.completed",
            )

        return payload

    except Exception:
        error_logs = stdout_buffer.getvalue() + "\n" + traceback.format_exc()

        _update_job(
            job_id,
            status="failure",
            logs=error_logs,
            error="harvest_failed",
            completed_at=datetime.now(UTC),
        )

        payload = {
            "job_id": job_id,
            "status": "failure",
            "model": model,
            "result": None,
            "logs": error_logs,
            "error": "harvest_failed",
        }

        sys.stderr.write(json.dumps(payload) + "\n")
        sys.stderr.flush()

        if webhook_url:
            dispatch_webhook.delay(
                payload=payload,
                webhook_url=webhook_url,
                webhook_secret=webhook_secret,
                event="job.failed",
            )

        raise


@shared_task(bind=True)
def run_harvester_task(
    self: Any,
    *,
    model: str,
    url: str,
    api_key: str,
    webhook_url: str | None = None,
    webhook_secret: str | None = None,
) -> dict[str, Any]:
    return _run_harvest(
        self.request.id,
        model=model,
        url=url,
        api_key=api_key,
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
    )
