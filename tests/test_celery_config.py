from llm_metadata_harvester_service.core.celery_app import celery_app
from llm_metadata_harvester_service.workers.tasks import run_harvester_task
from llm_metadata_harvester_service.workers.webhook import (
    deliver_job_webhook,
    publish_pending_webhooks,
)


def test_ephemeral_worker_configuration():
    assert celery_app.conf.task_acks_late is False
    assert celery_app.conf.task_reject_on_worker_lost is False
    assert celery_app.conf.task_default_delivery_mode == 1
    assert celery_app.conf.worker_prefetch_multiplier == 1
    schedule = celery_app.conf.beat_schedule["reconcile-stale-jobs"]
    assert schedule["schedule"] == 60
    assert schedule["options"]["expires"] == 60
    outbox_schedule = celery_app.conf.beat_schedule["publish-pending-webhooks"]
    assert outbox_schedule["schedule"] == 5


def test_tasks_are_bound_to_service_app():
    assert run_harvester_task.app is celery_app
    assert deliver_job_webhook.app is celery_app
    assert publish_pending_webhooks.app is celery_app
