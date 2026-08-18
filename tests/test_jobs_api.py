import uuid

import pytest
from fastapi.testclient import TestClient

from llm_metadata_harvester_service.api import routes
from llm_metadata_harvester_service.db.models import Job
from llm_metadata_harvester_service.db.session import SessionLocal
from llm_metadata_harvester_service.main import app

client = TestClient(app)
API_KEY_HEADER = {"X-API-Key": "test-key"}


class FakeTask:
    def __init__(self, job_id):
        self.id = job_id


class FakeTaskRunner:
    def __init__(self):
        self.calls = []

    def apply_async(self, *, kwargs, task_id):
        self.calls.append((task_id, kwargs))
        return FakeTask(task_id)


@pytest.fixture
def fake_runner(monkeypatch):
    runner = FakeTaskRunner()
    monkeypatch.setattr(routes.jobs, "run_harvester_task", runner)
    return runner


def _insert_job(job_id=None, **fields):
    job = Job(
        job_id=job_id or str(uuid.uuid4()),
        model=fields.get("model", "gemini-2.5-flash"),
        url=fields.get("url", "https://example.com"),
        status=fields.get("status", "queued"),
        result=fields.get("result"),
        logs=fields.get("logs"),
        error=fields.get("error"),
    )
    with SessionLocal() as db:
        db.add(job)
        db.commit()
        return job.job_id


def test_submit_job_creates_row_and_enqueues(fake_runner):
    resp = client.post(
        "/jobs/",
        json={
            "model": "gemini-2.5-flash",
            "url": "https://example.com",
            "webhook_url": "https://receiver.example.com/hook",
        },
        headers=API_KEY_HEADER,
    )

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["webhook_url"] == "https://receiver.example.com/hook"

    assert len(fake_runner.calls) == 1
    task_id, kwargs = fake_runner.calls[0]
    assert task_id == body["job_id"]
    assert kwargs["url"] == "https://example.com"
    assert kwargs["model"] == "gemini-2.5-flash"
    assert "test-key" not in kwargs["encrypted_api_key"]
    assert kwargs["webhook_url"] == "https://receiver.example.com/hook"
    assert kwargs["encrypted_webhook_secret"] is None

    with SessionLocal() as db:
        row = db.get(Job, body["job_id"])
        assert row is not None
        assert row.status == "queued"
        assert row.url == "https://example.com"
        assert row.batch_id is None


def test_submit_encrypts_webhook_secret(fake_runner):
    secret = "supersecretvalue123456"
    response = client.post(
        "/jobs/",
        json={
            "model": "gemini-2.5-flash",
            "url": "https://example.com",
            "webhook_url": "https://receiver.example.com/hook",
            "webhook_secret": secret,
        },
        headers=API_KEY_HEADER,
    )
    assert response.status_code == 202
    _, kwargs = fake_runner.calls[0]
    assert secret not in kwargs["encrypted_webhook_secret"]

def test_submit_job_rejects_bad_webhook_url():
    resp = client.post(
        "/jobs/",
        json={
            "model": "gemini-2.5-flash",
            "url": "https://example.com",
            "webhook_url": "ftp://receiver.example.com/hook",
        },
        headers=API_KEY_HEADER,
    )
    assert resp.status_code == 422


def test_submit_job_rejects_overlong_model():
    resp = client.post(
        "/jobs/",
        json={"model": "m" * 129, "url": "https://example.com"},
        headers=API_KEY_HEADER,
    )
    assert resp.status_code == 422


def test_submit_job_enqueue_failure_marks_dispatch_unconfirmed(monkeypatch):
    class FailingTaskRunner:
        def apply_async(self, *, kwargs, task_id):
            raise RuntimeError("broker unavailable")

    monkeypatch.setattr(routes.jobs, "run_harvester_task", FailingTaskRunner())
    resp = client.post(
        "/jobs/",
        json={"model": "gemini-2.5-flash", "url": "https://example.com"},
        headers=API_KEY_HEADER,
    )

    assert resp.status_code == 202
    assert resp.json()["status"] == "dispatch_unconfirmed"
    with SessionLocal() as db:
        row = db.query(Job).one()
        assert row.status == "queued"
        assert row.error == "dispatch_unconfirmed"


def test_get_status_returns_queued(fake_runner):
    job_id = client.post(
        "/jobs/",
        json={"model": "gemini-2.5-flash", "url": "https://example.com"},
        headers=API_KEY_HEADER,
    ).json()["job_id"]

    resp = client.get(f"/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json() == {"job_id": job_id, "status": "queued"}


def test_get_status_unknown_returns_404():
    resp = client.get("/jobs/does-not-exist")
    assert resp.status_code == 404


def test_get_result_pending_returns_202(fake_runner):
    job_id = client.post(
        "/jobs/",
        json={"model": "gemini-2.5-flash", "url": "https://example.com"},
        headers=API_KEY_HEADER,
    ).json()["job_id"]

    resp = client.get(f"/jobs/{job_id}/result")
    assert resp.status_code == 202
    assert resp.json()["detail"] == "Job still pending"


def test_get_result_unknown_returns_404():
    resp = client.get("/jobs/does-not-exist/result")
    assert resp.status_code == 404


def test_get_result_success():
    job_id = _insert_job(
        status="success",
        result={"title": "Example"},
        logs="done\n",
    )

    resp = client.get(f"/jobs/{job_id}/result")

    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == job_id
    assert body["status"] == "success"
    assert body["result"] == {"title": "Example"}
    assert body["logs"] == "done\n"


def test_get_result_failure_returns_500():
    job_id = _insert_job(status="failure", error="harvest_failed")

    resp = client.get(f"/jobs/{job_id}/result")

    assert resp.status_code == 500
    assert resp.json()["detail"] == "harvest_failed"
