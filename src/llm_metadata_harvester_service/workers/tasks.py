# src/llm_metadata_harvester_service/workers/tasks.py

import asyncio
import io
import json
import sys
import traceback
from contextlib import redirect_stdout
from typing import Any

from celery import shared_task
from llm_metadata_harvester.harvester_operations import metadata_harvest

from llm_metadata_harvester_service.workers.webhook import dispatch_webhook


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

        payload = {
            "job_id": self.request.id,
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

        payload = {
            "job_id": self.request.id,
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
