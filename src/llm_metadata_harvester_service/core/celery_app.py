import os

from celery import Celery

from llm_metadata_harvester_service.core.config import RESULT_EXPIRES_SECONDS

broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
result_backend = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")


celery_app = Celery(
    "llm_metadata_harvester_service",
    broker=broker_url,
    backend=result_backend,
)

celery_app.autodiscover_tasks(
    [
        "llm_metadata_harvester_service.workers",
    ]
)

celery_app.conf.result_expires = RESULT_EXPIRES_SECONDS

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
)
