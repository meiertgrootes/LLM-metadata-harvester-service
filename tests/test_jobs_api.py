import uuid

from fastapi.testclient import TestClient

from llm_metadata_harvester_service.api import routes
from llm_metadata_harvester_service.main import app

client = TestClient(app)
API_KEY_HEADER = {"X-API-Key": "test-key"}


class FakeTask:
    def __init__(self, job_id):
        self.id = job_id


class FakeTaskRunner:
    def __init__(self, captured, job_id):
        self.captured = captured
        self.job_id = job_id

    def delay(self, *, model, url, api_key, webhook_url, webhook_secret):
        self.captured.update(
            model=model,
            url=url,
            api_key=api_key,
            webhook_url=webhook_url,
            webhook_secret=webhook_secret,
        )
        return FakeTask(self.job_id)


class FakeAsyncResult:
    def __init__(self, state="SUCCESS", result=None, info=None):
        self.state = state
        self.result = result
        self.info = info


def _capture_delay(monkeypatch, captured, job_id=None):
    job_id = job_id or str(uuid.uuid4())
    monkeypatch.setattr(
        routes.jobs,
        "run_harvester_task",
        FakeTaskRunner(captured, job_id),
    )
    return job_id


def test_submit_with_webhook(monkeypatch):
    captured = {}
    job_id = _capture_delay(monkeypatch, captured)

    resp = client.post(
        "/jobs/",
        json={
            "model": "gemini-2.5-flash",
            "url": "https://example.com",
            "webhook_url": "https://receiver.example.com/hook",
            "webhook_secret": "supersecretvalue123456",
        },
        headers=API_KEY_HEADER,
    )

    assert resp.status_code == 202
    body = resp.json()
    assert body == {
        "job_id": job_id,
        "status": "queued",
        "webhook_url": "https://receiver.example.com/hook",
    }
    assert captured["webhook_url"] == "https://receiver.example.com/hook"
    assert captured["webhook_secret"] == "supersecretvalue123456"


def test_submit_without_webhook(monkeypatch):
    captured = {}
    job_id = _capture_delay(monkeypatch, captured)

    resp = client.post(
        "/jobs/",
        json={"model": "gemini-2.5-flash", "url": "https://example.com"},
        headers=API_KEY_HEADER,
    )

    assert resp.status_code == 202
    body = resp.json()
    assert body["job_id"] == job_id
    assert body["webhook_url"] is None
    assert captured["webhook_url"] is None
    assert captured["webhook_secret"] is None


def test_submit_rejects_non_http_webhook_url(monkeypatch):
    _capture_delay(monkeypatch, {})

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


def test_submit_rejects_too_short_webhook_secret(monkeypatch):
    _capture_delay(monkeypatch, {})

    resp = client.post(
        "/jobs/",
        json={
            "model": "gemini-2.5-flash",
            "url": "https://example.com",
            "webhook_url": "https://receiver.example.com/hook",
            "webhook_secret": "short",
        },
        headers=API_KEY_HEADER,
    )

    assert resp.status_code == 422


def test_get_status(monkeypatch):
    monkeypatch.setattr(
        routes.jobs,
        "AsyncResult",
        lambda job_id, app=None: FakeAsyncResult(state="PENDING"),
    )

    resp = client.get("/jobs/abc")

    assert resp.status_code == 200
    assert resp.json() == {"job_id": "abc", "status": "pending"}


def test_get_result_success(monkeypatch):
    monkeypatch.setattr(
        routes.jobs,
        "AsyncResult",
        lambda job_id, app=None: FakeAsyncResult(
            state="SUCCESS",
            result={"model": "gemini-2.5-flash", "result": {"title": "Example"}, "logs": "ok"},
        ),
    )

    resp = client.get("/jobs/abc/result")

    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == "abc"
    assert body["status"] == "success"
    assert body["model"] == "gemini-2.5-flash"
    assert body["result"] == {"title": "Example"}
    assert body["logs"] == "ok"


def test_get_result_pending(monkeypatch):
    monkeypatch.setattr(
        routes.jobs,
        "AsyncResult",
        lambda job_id, app=None: FakeAsyncResult(state="PENDING"),
    )

    resp = client.get("/jobs/abc/result")

    assert resp.status_code == 202
    assert resp.json()["detail"] == "Job still pending"


def test_get_result_failure(monkeypatch):
    monkeypatch.setattr(
        routes.jobs,
        "AsyncResult",
        lambda job_id, app=None: FakeAsyncResult(state="FAILURE", info="boom"),
    )

    resp = client.get("/jobs/abc/result")

    assert resp.status_code == 500
