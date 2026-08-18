import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from llm_metadata_harvester_service.core.secrets import encrypt_task_secret
from llm_metadata_harvester_service.db.models import Job
from llm_metadata_harvester_service.db.session import SessionLocal
from llm_metadata_harvester_service.workers import tasks


def test_active_redelivery_does_not_start_second_harvest(monkeypatch):
    job_id = str(uuid.uuid4())
    _add_job(job_id)
    with SessionLocal() as db:
        row = db.get(Job, job_id)
        row.status = "pending"
        row.execution_attempts = 1
        db.commit()

    async def unexpected_harvest(**kwargs):
        raise AssertionError("active job was harvested again")

    monkeypatch.setattr(tasks, "metadata_harvest", unexpected_harvest)
    payload = tasks._run_harvest(
        job_id,
        model="gemini-2.5-flash",
        url="https://example.com",
        encrypted_api_key=encrypt_task_secret("k"),
    )

    assert payload["status"] == "pending"
    assert _get_job(job_id).execution_attempts == 1


def test_broker_redelivery_reclaims_pending_job(monkeypatch):
    job_id = str(uuid.uuid4())
    _add_job(job_id)
    with SessionLocal() as db:
        row = db.get(Job, job_id)
        row.status = "pending"
        row.execution_attempts = 1
        db.commit()

    async def fake_harvest(*, model_name, url, api_key):
        return {"title": "Recovered"}

    monkeypatch.setattr(tasks, "metadata_harvest", fake_harvest)
    payload = tasks._run_harvest(
        job_id,
        model="gemini-2.5-flash",
        url="https://example.com",
        encrypted_api_key=encrypt_task_secret("k"),
        allow_pending_reclaim=True,
    )

    assert payload["status"] == "success"
    assert _get_job(job_id).execution_attempts == 2


def _add_job(job_id: str):
    with SessionLocal() as db:
        db.add(
            Job(
                job_id=job_id,
                model="gemini-2.5-flash",
                url="https://example.com",
                status="queued",
            )
        )
        db.commit()


def _get_job(job_id: str):
    with SessionLocal() as db:
        return db.get(Job, job_id)


def _noop_dispatch(monkeypatch, calls=None):
    if calls is None:
        calls = []

    def fake_apply_async(*, kwargs, task_id):
        calls.append(kwargs)

    monkeypatch.setattr(
        tasks, "dispatch_webhook", SimpleNamespace(apply_async=fake_apply_async)
    )
    return calls


def test_run_harvest_success_writes_result(monkeypatch):
    job_id = str(uuid.uuid4())
    _add_job(job_id)

    async def fake_harvest(*, model_name, url, api_key):
        return {"title": "Example", "status": "ok"}

    monkeypatch.setattr(tasks, "metadata_harvest", fake_harvest)
    _noop_dispatch(monkeypatch)

    payload = tasks._run_harvest(
        job_id,
        model="gemini-2.5-flash",
        url="https://example.com",
        encrypted_api_key=encrypt_task_secret("k"),
    )

    assert payload["job_id"] == job_id
    assert payload["status"] == "success"

    row = _get_job(job_id)
    assert row.status == "success"
    assert row.result == {"title": "Example", "status": "ok"}
    assert row.completed_at is not None


def test_run_harvest_failure_writes_error(monkeypatch):
    job_id = str(uuid.uuid4())
    _add_job(job_id)

    async def fake_harvest(*, model_name, url, api_key):
        raise RuntimeError("boom")

    monkeypatch.setattr(tasks, "metadata_harvest", fake_harvest)
    _noop_dispatch(monkeypatch)

    with pytest.raises(RuntimeError):
        tasks._run_harvest(
            job_id,
            model="gemini-2.5-flash",
            url="https://example.com",
            encrypted_api_key=encrypt_task_secret("k"),
        )

    row = _get_job(job_id)
    assert row.status == "failure"
    assert row.error == "harvest_failed"
    assert "RuntimeError: boom" in (row.logs or "")
    assert row.completed_at is not None


def test_run_harvest_dispatches_webhook_on_success(monkeypatch):
    job_id = str(uuid.uuid4())
    _add_job(job_id)

    async def fake_harvest(*, model_name, url, api_key):
        return {"title": "Example"}

    monkeypatch.setattr(tasks, "metadata_harvest", fake_harvest)
    calls = _noop_dispatch(monkeypatch)

    tasks._run_harvest(
        job_id,
        model="gemini-2.5-flash",
        url="https://example.com",
        encrypted_api_key=encrypt_task_secret("k"),
        webhook_url="https://receiver.example.com/hook",
        encrypted_webhook_secret=encrypt_task_secret("supersecretvalue123456"),
    )

    assert len(calls) == 1
    assert calls[0]["event"] == "job.completed"
    assert calls[0]["webhook_url"] == "https://receiver.example.com/hook"
    assert "supersecretvalue123456" not in calls[0]["encrypted_webhook_secret"]
    assert calls[0]["payload"]["job_id"] == job_id


def test_run_harvest_dispatches_webhook_on_failure(monkeypatch):
    job_id = str(uuid.uuid4())
    _add_job(job_id)

    async def fake_harvest(*, model_name, url, api_key):
        raise RuntimeError("boom")

    monkeypatch.setattr(tasks, "metadata_harvest", fake_harvest)
    calls = _noop_dispatch(monkeypatch)

    with pytest.raises(RuntimeError):
        tasks._run_harvest(
            job_id,
            model="gemini-2.5-flash",
            url="https://example.com",
            encrypted_api_key=encrypt_task_secret("k"),
            webhook_url="https://receiver.example.com/hook",
        )

    assert len(calls) == 1
    assert calls[0]["event"] == "job.failed"


def test_run_harvest_without_webhook_does_not_dispatch(monkeypatch):
    job_id = str(uuid.uuid4())
    _add_job(job_id)

    async def fake_harvest(*, model_name, url, api_key):
        return {"title": "Example"}

    monkeypatch.setattr(tasks, "metadata_harvest", fake_harvest)
    calls = _noop_dispatch(monkeypatch)

    tasks._run_harvest(
        job_id,
        model="gemini-2.5-flash",
        url="https://example.com",
        encrypted_api_key=encrypt_task_secret("k"),
    )

    assert calls == []


def test_webhook_enqueue_failure_does_not_overwrite_success(monkeypatch):
    job_id = str(uuid.uuid4())
    _add_job(job_id)

    async def fake_harvest(*, model_name, url, api_key):
        return {"title": "Example"}

    class FailingWebhook:
        @staticmethod
        def apply_async(*, kwargs, task_id):
            raise RuntimeError("broker unavailable")

    monkeypatch.setattr(tasks, "metadata_harvest", fake_harvest)
    monkeypatch.setattr(tasks, "dispatch_webhook", FailingWebhook())

    payload = tasks._run_harvest(
        job_id,
        model="gemini-2.5-flash",
        url="https://example.com",
        encrypted_api_key=encrypt_task_secret("k"),
        webhook_url="https://receiver.example.com/hook",
    )

    assert payload["status"] == "success"
    row = _get_job(job_id)
    assert row.status == "success"
    assert row.webhook_error == "dispatch_failed"


def test_terminal_redelivery_does_not_run_harvester(monkeypatch):
    job_id = str(uuid.uuid4())
    _add_job(job_id)
    with SessionLocal() as db:
        row = db.get(Job, job_id)
        row.status = "success"
        row.result = {"title": "Already done"}
        db.commit()

    async def unexpected_harvest(**kwargs):
        raise AssertionError("terminal job was harvested again")

    monkeypatch.setattr(tasks, "metadata_harvest", unexpected_harvest)
    payload = tasks._run_harvest(
        job_id,
        model="gemini-2.5-flash",
        url="https://example.com",
        encrypted_api_key=encrypt_task_secret("k"),
    )

    assert payload["result"] == {"title": "Already done"}
    assert _get_job(job_id).execution_attempts == 0


def test_reconcile_stale_jobs_marks_pending_and_queued():
    now = datetime.now(UTC)
    with SessionLocal() as db:
        db.add_all(
            [
                Job(
                    job_id="old-pending",
                    model="m",
                    url="https://example.com",
                    status="pending",
                    updated_at=now - timedelta(days=2),
                ),
                Job(
                    job_id="old-queued",
                    model="m",
                    url="https://example.com",
                    status="queued",
                    updated_at=now - timedelta(days=2),
                ),
                Job(
                    job_id="recent-pending",
                    model="m",
                    url="https://example.com",
                    status="pending",
                    updated_at=now,
                ),
            ]
        )
        db.commit()

    assert tasks.reconcile_stale_jobs.run() == 2
    assert _get_job("old-pending").error == "worker_lost"
    assert _get_job("old-queued").error == "dispatch_lost"
    assert _get_job("recent-pending").status == "pending"
