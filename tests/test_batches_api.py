import uuid

import pytest
from fastapi.testclient import TestClient

from llm_metadata_harvester_service.api import routes
from llm_metadata_harvester_service.core.config import BATCH_MAX_URLS
from llm_metadata_harvester_service.db.models import Job
from llm_metadata_harvester_service.db.session import SessionLocal
from llm_metadata_harvester_service.main import app

client = TestClient(app)
API_KEY_HEADER = {"X-API-Key": "test-key"}


class FakeTaskRunner:
    def __init__(self):
        self.calls = []

    def apply_async(self, *, kwargs, task_id):
        self.calls.append((task_id, kwargs))
        return type("Task", (), {"id": task_id})()


@pytest.fixture
def fake_runner(monkeypatch):
    runner = FakeTaskRunner()
    monkeypatch.setattr(routes.jobs, "run_harvester_task", runner)
    return runner


def _insert_batch_jobs(batch_id: str, spec: list[dict]):
    with SessionLocal() as db:
        for item in spec:
            db.add(
                Job(
                    job_id=item["job_id"],
                    batch_id=batch_id,
                    model=item.get("model", "gemini-2.5-flash"),
                    url=item["url"],
                    status=item.get("status", "queued"),
                    result=item.get("result"),
                    logs=item.get("logs"),
                    error=item.get("error"),
                )
            )
        db.commit()


def test_submit_batch_creates_individual_jobs(fake_runner):
    urls = ["https://example.com", "https://example.org", "https://example.net"]

    resp = client.post(
        "/jobs/batch/",
        json={"model": "gemini-2.5-flash", "urls": urls},
        headers=API_KEY_HEADER,
    )

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["count"] == 3
    assert len(body["jobs"]) == 3

    job_ids = [job["job_id"] for job in body["jobs"]]
    assert len(set(job_ids)) == 3
    assert {job["url"] for job in body["jobs"]} == set(urls)

    assert len(fake_runner.calls) == 3
    dispatched = {task_id for task_id, _ in fake_runner.calls}
    assert dispatched == set(job_ids)

    with SessionLocal() as db:
        rows = db.query(Job).filter(Job.batch_id == body["batch_id"]).all()
        assert len(rows) == 3
        assert all(row.status == "queued" for row in rows)
        assert {row.url for row in rows} == set(urls)


def test_submit_batch_enqueues_fields_for_every_job(fake_runner):
    fields = ["Title", "License"]
    response = client.post(
        "/jobs/batch/",
        json={
            "model": "gemini-3.5-flash-lite",
            "urls": ["https://example.com", "https://example.org"],
            "fields": fields,
        },
        headers=API_KEY_HEADER,
    )

    assert response.status_code == 202
    assert len(fake_runner.calls) == 2
    assert all(kwargs["fields"] == fields for _, kwargs in fake_runner.calls)


def test_submit_batch_rejects_empty():
    resp = client.post(
        "/jobs/batch/",
        json={"model": "gemini-2.5-flash", "urls": []},
        headers=API_KEY_HEADER,
    )
    assert resp.status_code == 422


def test_submit_batch_rejects_empty_fields():
    response = client.post(
        "/jobs/batch/",
        json={
            "model": "gemini-3.5-flash-lite",
            "urls": ["https://example.com"],
            "fields": [],
        },
        headers=API_KEY_HEADER,
    )

    assert response.status_code == 422


def test_submit_batch_rejects_over_limit():
    urls = [f"https://example.com/{i}" for i in range(BATCH_MAX_URLS + 1)]
    resp = client.post(
        "/jobs/batch/",
        json={"model": "gemini-2.5-flash", "urls": urls},
        headers=API_KEY_HEADER,
    )
    assert resp.status_code == 422


def test_submit_batch_reports_partial_dispatch_failure(monkeypatch):
    class PartiallyFailingTaskRunner:
        calls = 0

        def apply_async(self, *, kwargs, task_id):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("broker unavailable")
            return type("Task", (), {"id": task_id})()

    monkeypatch.setattr(
        routes.jobs, "run_harvester_task", PartiallyFailingTaskRunner()
    )
    resp = client.post(
        "/jobs/batch/",
        json={
            "model": "gemini-2.5-flash",
            "urls": ["https://a.example", "https://b.example"],
        },
        headers=API_KEY_HEADER,
    )

    assert resp.status_code == 202
    assert resp.json()["status"] == "dispatch_unconfirmed"
    assert [job["status"] for job in resp.json()["jobs"]] == [
        "queued",
        "queued",
    ]


def test_submit_batch_reports_unconfirmed_dispatch(monkeypatch):
    class FailingTaskRunner:
        def apply_async(self, *, kwargs, task_id):
            raise RuntimeError("broker unavailable")

    monkeypatch.setattr(routes.jobs, "run_harvester_task", FailingTaskRunner())
    resp = client.post(
        "/jobs/batch/",
        json={
            "model": "gemini-2.5-flash",
            "urls": ["https://a.example", "https://b.example"],
        },
        headers=API_KEY_HEADER,
    )

    assert resp.status_code == 202
    assert resp.json()["status"] == "dispatch_unconfirmed"


def test_get_batch_status_summary():
    batch_id = str(uuid.uuid4())
    _insert_batch_jobs(
        batch_id,
        [
            {"job_id": "j1", "url": "https://a.example", "status": "queued"},
            {"job_id": "j2", "url": "https://b.example", "status": "pending"},
            {"job_id": "j3", "url": "https://c.example", "status": "success"},
            {"job_id": "j4", "url": "https://d.example", "status": "failure"},
        ],
    )

    resp = client.get(f"/batches/{batch_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["batch_id"] == batch_id
    assert body["summary"] == {
        "queued": 1,
        "pending": 1,
        "success": 1,
        "failed": 1,
        "total": 4,
    }
    assert len(body["jobs"]) == 4


def test_get_batch_results_are_individual():
    batch_id = str(uuid.uuid4())
    _insert_batch_jobs(
        batch_id,
        [
            {
                "job_id": "j1",
                "url": "https://a.example",
                "status": "success",
                "result": {"title": "A"},
                "logs": "a\n",
            },
            {
                "job_id": "j2",
                "url": "https://b.example",
                "status": "success",
                "result": {"title": "B"},
                "logs": "b\n",
            },
            {
                "job_id": "j3",
                "url": "https://c.example",
                "status": "failure",
                "error": "harvest_failed",
            },
        ],
    )

    resp = client.get(f"/batches/{batch_id}/results")

    assert resp.status_code == 200
    body = resp.json()
    assert body["batch_id"] == batch_id
    assert len(body["jobs"]) == 3

    by_url = {job["url"]: job for job in body["jobs"]}
    assert by_url["https://a.example"]["result"] == {"title": "A"}
    assert by_url["https://b.example"]["result"] == {"title": "B"}
    assert by_url["https://c.example"]["status"] == "failure"
    assert by_url["https://c.example"]["error"] == "harvest_failed"


def test_get_batch_unknown_returns_404():
    resp = client.get("/batches/does-not-exist")
    assert resp.status_code == 404
    resp = client.get("/batches/does-not-exist/results")
    assert resp.status_code == 404
