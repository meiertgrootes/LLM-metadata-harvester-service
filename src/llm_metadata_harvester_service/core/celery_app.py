import os
from typing import Any

from celery import Celery
from celery.signals import worker_process_init

broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")


celery_app = Celery(
    "llm_metadata_harvester_service",
    broker=broker_url,
    # Results are persisted to PostgreSQL by the worker itself; no result
    # backend is used. Redis serves only as the broker (task queue).
    backend=None,
)

celery_app.autodiscover_tasks(
    [
        "llm_metadata_harvester_service.workers",
    ]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
)


@worker_process_init.connect
def _init_db_on_worker_start(self: Any, **kwargs: Any) -> None:
    from llm_metadata_harvester_service.db.init import init_db

    init_db()
