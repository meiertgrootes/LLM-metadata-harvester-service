from llm_metadata_harvester_service.core.celery_app import celery_app
from llm_metadata_harvester_service.workers.tasks import run_harvester_task
from llm_metadata_harvester_service.workers.webhook import dispatch_webhook


def test_durable_worker_configuration():
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.broker_transport_options["visibility_timeout"] == 3900
    schedule = celery_app.conf.beat_schedule["reconcile-stale-jobs"]
    assert schedule["schedule"] == 60
    assert schedule["options"]["expires"] == 60


def test_tasks_are_bound_to_service_app():
    assert run_harvester_task.app is celery_app
    assert dispatch_webhook.app is celery_app
