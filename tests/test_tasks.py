import uuid
from datetime import UTC, datetime, timedelta

import pytest

from llm_metadata_harvester_service.core.secrets import encrypt_webhook_secret
from llm_metadata_harvester_service.db.models import Job, WebhookOutbox
from llm_metadata_harvester_service.db.session import SessionLocal
from llm_metadata_harvester_service.workers import tasks


def _add_job(
    job_id: str,
    *,
    status: str = "queued",
    webhook: bool = False,
    updated_at: datetime | None = None,
) -> None:
    with SessionLocal() as db:
        db.add(
            Job(
                job_id=job_id,
                model="gemini-2.5-flash",
                url="https://example.com",
                status=status,
                webhook_url=(
                    "https://receiver.example.com/hook" if webhook else None
                ),
                webhook_secret_encrypted=(
                    encrypt_webhook_secret("supersecretvalue123456")
                    if webhook
                    else None
                ),
                updated_at=updated_at or datetime.now(UTC),
            )
        )
        db.commit()


def _get_job(job_id: str) -> Job:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        assert job is not None
        db.expunge(job)
        return job


def _get_outbox(job_id: str) -> WebhookOutbox | None:
    with SessionLocal() as db:
        row = db.get(WebhookOutbox, job_id)
        if row is not None:
            db.expunge(row)
        return row


def test_run_harvest_success_writes_result_and_uses_plain_api_key(monkeypatch):
    job_id = str(uuid.uuid4())
    _add_job(job_id)
    captured = {}

    async def fake_harvest(*, model_name, url, api_key):
        captured["api_key"] = api_key
        return {"title": "Example"}

    monkeypatch.setattr(tasks, "metadata_harvest", fake_harvest)
    payload = tasks._run_harvest(
        job_id,
        model="gemini-2.5-flash",
        url="https://example.com",
        api_key="ephemeral-key",
    )

    assert payload["status"] == "success"
    assert captured["api_key"] == "ephemeral-key"
    assert _get_job(job_id).result == {"title": "Example"}
    assert _get_outbox(job_id) is None


def test_success_creates_transactional_webhook_outbox(monkeypatch):
    job_id = str(uuid.uuid4())
    _add_job(job_id, webhook=True)

    async def fake_harvest(**kwargs):
        return {"title": "Example"}

    monkeypatch.setattr(tasks, "metadata_harvest", fake_harvest)
    tasks._run_harvest(
        job_id,
        model="gemini-2.5-flash",
        url="https://example.com",
        api_key="k",
    )

    job = _get_job(job_id)
    outbox = _get_outbox(job_id)
    assert job.status == "success"
    assert job.webhook_secret_encrypted is None
    assert outbox is not None
    assert outbox.event == "job.completed"
    assert "supersecretvalue123456" not in (outbox.encrypted_secret or "")


def test_failure_creates_transactional_webhook_outbox(monkeypatch):
    job_id = str(uuid.uuid4())
    _add_job(job_id, webhook=True)

    async def fake_harvest(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(tasks, "metadata_harvest", fake_harvest)
    with pytest.raises(RuntimeError):
        tasks._run_harvest(
            job_id,
            model="gemini-2.5-flash",
            url="https://example.com",
            api_key="k",
        )

    assert _get_job(job_id).error == "harvest_failed"
    assert _get_outbox(job_id).event == "job.failed"


def test_outbox_failure_rolls_back_terminal_transition(monkeypatch):
    job_id = str(uuid.uuid4())
    _add_job(job_id, webhook=True)
    attempt, _ = tasks._claim_job(job_id)
    assert attempt == 1

    def fail(*args, **kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(tasks, "_ensure_webhook_outbox", fail)
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        tasks._complete_job(
            job_id,
            attempt,
            status="success",
            result={"title": "Example"},
            logs="",
            error=None,
        )

    job = _get_job(job_id)
    assert job.status == "pending"
    assert job.result is None


def test_pending_duplicate_does_not_restart_harvest(monkeypatch):
    job_id = str(uuid.uuid4())
    _add_job(job_id, status="pending")

    async def unexpected_harvest(**kwargs):
        raise AssertionError("pending job was harvested again")

    monkeypatch.setattr(tasks, "metadata_harvest", unexpected_harvest)
    payload = tasks._run_harvest(
        job_id,
        model="gemini-2.5-flash",
        url="https://example.com",
        api_key="k",
    )
    assert payload["status"] == "pending"


def test_terminal_duplicate_does_not_restart_harvest(monkeypatch):
    job_id = str(uuid.uuid4())
    _add_job(job_id, status="success")

    async def unexpected_harvest(**kwargs):
        raise AssertionError("terminal job was harvested again")

    monkeypatch.setattr(tasks, "metadata_harvest", unexpected_harvest)
    assert tasks._run_harvest(
        job_id,
        model="gemini-2.5-flash",
        url="https://example.com",
        api_key="k",
    )["status"] == "success"


def test_reconcile_stale_jobs_creates_failure_outbox():
    old = datetime.now(UTC) - timedelta(days=2)
    _add_job("old-pending", status="pending", webhook=True, updated_at=old)
    _add_job("old-queued", status="queued", webhook=True, updated_at=old)
    _add_job("recent", status="pending", updated_at=datetime.now(UTC))

    assert tasks.reconcile_stale_jobs.run() == 2
    assert _get_job("old-pending").error == "worker_lost"
    assert _get_job("old-queued").error == "dispatch_lost"
    assert _get_outbox("old-pending").event == "job.failed"
    assert _get_outbox("old-queued").event == "job.failed"
    assert _get_job("recent").status == "pending"
