from celery import Celery

from llm_metadata_harvester_service.core.config import (
    CELERY_BROKER_URL,
    CELERY_TASK_SOFT_TIME_LIMIT_SECONDS,
    CELERY_TASK_TIME_LIMIT_SECONDS,
    CELERY_VISIBILITY_TIMEOUT_SECONDS,
    JOB_RECONCILE_INTERVAL_SECONDS,
)

celery_app = Celery(
    "llm_metadata_harvester_service",
    broker=CELERY_BROKER_URL,
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
    task_acks_late=True,
    task_acks_on_failure_or_timeout=True,
    task_reject_on_worker_lost=True,
    task_default_delivery_mode=2,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=CELERY_TASK_SOFT_TIME_LIMIT_SECONDS,
    task_time_limit=CELERY_TASK_TIME_LIMIT_SECONDS,
    broker_transport_options={
        "visibility_timeout": CELERY_VISIBILITY_TIMEOUT_SECONDS
    },
    beat_schedule={
        "reconcile-stale-jobs": {
            "task": (
                "llm_metadata_harvester_service.workers.tasks."
                "reconcile_stale_jobs"
            ),
            "schedule": JOB_RECONCILE_INTERVAL_SECONDS,
            "options": {"expires": JOB_RECONCILE_INTERVAL_SECONDS},
        }
    },
)
