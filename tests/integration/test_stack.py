from __future__ import annotations

import json
from typing import Any

import pytest

from .conftest import Stack

pytestmark = pytest.mark.integration
API_HEADERS = {"X-API-Key": "fake-provider-key-never-sent-off-stack"}
REGISTERED_TASKS = {
    "llm_metadata_harvester_service.workers.tasks.reconcile_stale_jobs",
    "llm_metadata_harvester_service.workers.tasks.run_harvester_task",
    "llm_metadata_harvester_service.workers.webhook.deliver_job_webhook",
    "llm_metadata_harvester_service.workers.webhook.publish_pending_webhooks",
}


@pytest.mark.migration
def test_postgresql_fresh_and_legacy_migrations(stack: Stack) -> None:
    assert stack.sql("SELECT version_num FROM alembic_version") == "0003"

    stack.sql("CREATE DATABASE migration_fresh")
    fresh_url = (
        "postgresql+psycopg2://harvester:harvester@postgres:5432/migration_fresh"
    )
    stack.compose(
        "run",
        "--rm",
        "-e",
        f"DATABASE_URL={fresh_url}",
        "migrate",
        "alembic",
        "upgrade",
        "head",
    )
    assert stack.sql(
        "SELECT version_num FROM alembic_version", database="migration_fresh"
    ) == "0003"

    stack.sql("CREATE DATABASE migration_legacy")
    legacy_url = (
        "postgresql+psycopg2://harvester:harvester@postgres:5432/migration_legacy"
    )
    stack.compose(
        "run",
        "--rm",
        "-e",
        f"DATABASE_URL={legacy_url}",
        "migrate",
        "alembic",
        "upgrade",
        "0001",
    )
    stack.sql(
        "INSERT INTO jobs (job_id, model, url, status, webhook_attempts, "
        "created_at, updated_at) VALUES ('legacy-job', 'legacy-model', "
        "'https://legacy.example', 'queued', 0, now(), now())",
        database="migration_legacy",
    )
    stack.compose(
        "run",
        "--rm",
        "-e",
        f"DATABASE_URL={legacy_url}",
        "migrate",
        "alembic",
        "upgrade",
        "head",
    )
    assert stack.sql(
        "SELECT version_num FROM alembic_version", database="migration_legacy"
    ) == "0003"
    assert stack.sql(
        "SELECT job_id || '|' || execution_attempts FROM jobs "
        "WHERE job_id = 'legacy-job'",
        database="migration_legacy",
    ) == "legacy-job|0"
    assert stack.sql(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'webhook_outbox'",
        database="migration_legacy",
    ) == "1"

    for database in ("harvester", "migration_fresh", "migration_legacy"):
        assert stack.sql(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name ILIKE '%api_key%'",
            database=database,
        ) == "0"


@pytest.mark.smoke
def test_compose_health_worker_redis_and_volumes(stack: Stack) -> None:
    assert stack.request("GET", "/health/live") == (200, {"status": "alive"})
    assert stack.request("GET", "/health/ready") == (
        200,
        {"status": "ready", "checks": {"postgres": "ok", "redis": "ok"}},
    )

    def registered() -> str | None:
        result = stack.compose(
            "exec",
            "-T",
            "worker",
            "celery",
            "-A",
            "llm_metadata_harvester_service.core.celery_app.celery_app",
            "inspect",
            "registered",
            "--timeout",
            "3",
            check=False,
        )
        return result.stdout if result.returncode == 0 else None

    registration = stack.wait_for("worker task registration", registered)
    assert all(task in registration for task in REGISTERED_TASKS)

    redis_config = stack.compose(
        "exec", "-T", "redis", "redis-cli", "--raw", "CONFIG", "GET", "save"
    ).stdout.splitlines()
    appendonly = stack.compose(
        "exec",
        "-T",
        "redis",
        "redis-cli",
        "--raw",
        "CONFIG",
        "GET",
        "appendonly",
    ).stdout.splitlines()
    assert redis_config == ["save", ""]
    assert appendonly == ["appendonly", "no"]

    redis_id = stack.compose("ps", "-q", "redis").stdout.strip()
    postgres_id = stack.compose("ps", "-q", "postgres").stdout.strip()
    redis_mounts = json.loads(
        stack.docker("inspect", redis_id, "--format", "{{json .Mounts}}").stdout
    )
    postgres_mounts = json.loads(
        stack.docker("inspect", postgres_id, "--format", "{{json .Mounts}}").stdout
    )
    redis_tmpfs = json.loads(
        stack.docker(
            "inspect", redis_id, "--format", "{{json .HostConfig.Tmpfs}}"
        ).stdout
    )
    assert redis_mounts == []
    assert "/data" in redis_tmpfs
    assert [mount["Destination"] for mount in postgres_mounts] == [
        "/var/lib/postgresql/data"
    ]


@pytest.mark.e2e
def test_single_and_batch_jobs_use_only_the_stub(stack: Stack) -> None:
    requested_fields = ["Title", "Description", "License"]
    status, submitted = stack.request(
        "POST",
        "/jobs/",
        payload={
            "model": "stub-model",
            "url": "https://single.example/data",
            "fields": requested_fields,
        },
        headers=API_HEADERS,
    )
    assert status == 202
    stack.wait_for_job(submitted["job_id"])
    status, result = stack.request("GET", f"/jobs/{submitted['job_id']}/result")
    assert status == 200
    assert result["result"] == {
        "source": "integration-stub",
        "model": "stub-model",
        "url": "https://single.example/data",
    }
    assert "deterministic harvester stub" in result["logs"]

    urls = ["https://batch.example/one", "stub-failure://batch/two"]
    status, batch = stack.request(
        "POST",
        "/jobs/batch/",
        payload={"model": "stub-model", "urls": urls},
        headers=API_HEADERS,
    )
    assert status == 202
    assert batch["count"] == 2
    assert len({job["job_id"] for job in batch["jobs"]}) == 2
    for job in batch["jobs"]:
        stack.wait_for_job(job["job_id"])

    _, overview = stack.request("GET", f"/batches/{batch['batch_id']}")
    assert overview["summary"] == {
        "queued": 0,
        "pending": 0,
        "success": 1,
        "failed": 1,
        "total": 2,
    }
    _, results = stack.request("GET", f"/batches/{batch['batch_id']}/results")
    assert {job["url"] for job in results["jobs"]} == set(urls)
    assert {job["status"] for job in results["jobs"]} == {"success", "failure"}
    calls = stack.state()["harvester_calls"]
    assert all(call["api_key_supplied"] for call in calls)
    assert "fake-provider-key" not in json.dumps(calls)
    single_call = next(
        call for call in calls if call["url"] == "https://single.example/data"
    )
    assert single_call["fields"] == requested_fields


@pytest.mark.e2e
@pytest.mark.resilience
def test_webhook_outbox_recovers_after_redis_loss(stack: Stack) -> None:
    stack.compose("stop", "beat")
    try:
        stack.compose("restart", "redis")
        stack.wait_for_api_and_worker()
        status, submitted = stack.request(
            "POST",
            "/jobs/",
            payload={
                "model": "stub-model",
                "url": "https://webhook.example/data",
                "webhook_url": "http://receiver:8080/webhook",
                "webhook_secret": "integration-webhook-secret",
            },
            headers=API_HEADERS,
        )
        assert status == 202
        job_id = submitted["job_id"]
        assert stack.wait_for_job(job_id)["status"] == "success"
        before = stack.sql(
            "SELECT attempts, delivered_at IS NULL, encrypted_secret IS NOT NULL "
            f"FROM webhook_outbox WHERE job_id = '{job_id}'"
        )
        assert before == "0|t|t"

        stack.compose("restart", "redis")
        stack.wait_for_api_and_worker()
        stack.compose("start", "beat")

        def matching_delivery() -> dict[str, Any] | None:
            for delivery in stack.state()["deliveries"]:
                if delivery["payload"]["job_id"] == job_id:
                    return delivery
            return None

        delivery = stack.wait_for("signed webhook delivery", matching_delivery)
        assert delivery["signature_valid"] is True
        assert delivery["signature"].startswith("sha256=")
        assert delivery["payload"]["event"] == "job.completed"
        assert delivery["payload"]["timestamp"]

        outbox = stack.sql(
            "SELECT attempts, published_at IS NOT NULL, "
            "delivery_started_at IS NOT NULL, delivered_at IS NOT NULL, "
            "created_at IS NOT NULL, updated_at IS NOT NULL, "
            "encrypted_secret IS NULL, last_error IS NULL "
            f"FROM webhook_outbox WHERE job_id = '{job_id}'"
        )
        assert outbox == "1|t|t|t|t|t|t|t"
        job_webhook = stack.sql(
            "SELECT webhook_attempts, webhook_last_attempt_at IS NOT NULL, "
            "webhook_delivered_at IS NOT NULL, webhook_secret_encrypted IS NULL "
            f"FROM jobs WHERE job_id = '{job_id}'"
        )
        assert job_webhook == "1|t|t|t"
    finally:
        stack.compose("start", "beat", check=False)


@pytest.mark.e2e
@pytest.mark.resilience
def test_queued_harvest_is_lost_with_redis_and_reconciled(stack: Stack) -> None:
    calls_before = len(stack.state()["harvester_calls"])
    stack.compose("stop", "worker")
    try:
        status, submitted = stack.request(
            "POST",
            "/jobs/",
            payload={"model": "stub-model", "url": "https://lost.example/data"},
            headers=API_HEADERS,
        )
        assert status == 202
        job_id = submitted["job_id"]
        assert stack.request("GET", f"/jobs/{job_id}")[1]["status"] == "queued"
        queued = int(
            stack.compose(
                "exec", "-T", "redis", "redis-cli", "--raw", "LLEN", "celery"
            ).stdout.strip()
        )
        assert queued >= 1

        stack.compose("restart", "redis")
        stack.compose("start", "worker")
        assert stack.wait_for_job(job_id, timeout=20)["status"] == "failure"
        result_status, result = stack.request("GET", f"/jobs/{job_id}/result")
        assert result_status == 500
        assert result == {"detail": "dispatch_lost"}
        assert stack.sql(
            f"SELECT error FROM jobs WHERE job_id = '{job_id}'"
        ) == "dispatch_lost"
        assert len(stack.state()["harvester_calls"]) == calls_before
    finally:
        stack.compose("start", "worker", check=False)
