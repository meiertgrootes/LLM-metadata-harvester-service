import uuid
from types import SimpleNamespace

import pytest

from llm_metadata_harvester_service.db.models import Job
from llm_metadata_harvester_service.db.session import SessionLocal
from llm_metadata_harvester_service.workers import tasks


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

    def fake_delay(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(tasks, "dispatch_webhook", SimpleNamespace(delay=fake_delay))
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
        api_key="k",
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
            api_key="k",
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
        api_key="k",
        webhook_url="https://receiver.example.com/hook",
        webhook_secret="supersecretvalue123456",
    )

    assert len(calls) == 1
    assert calls[0]["event"] == "job.completed"
    assert calls[0]["webhook_url"] == "https://receiver.example.com/hook"
    assert calls[0]["webhook_secret"] == "supersecretvalue123456"
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
            api_key="k",
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
        api_key="k",
    )

    assert calls == []
