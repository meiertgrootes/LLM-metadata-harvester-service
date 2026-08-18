import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx

from llm_metadata_harvester_service.core.secrets import encrypt_webhook_secret
from llm_metadata_harvester_service.db.models import Job, WebhookOutbox
from llm_metadata_harvester_service.db.session import SessionLocal
from llm_metadata_harvester_service.workers import webhook
from llm_metadata_harvester_service.workers.webhook import (
    DeliveryOutcome,
    _build_body,
    _deliver_once,
    _parse_retry_after,
    sign_payload,
)

PAYLOAD = {
    "job_id": "job-1",
    "status": "success",
    "model": "gemini-2.5-flash",
    "result": {"title": "Example"},
    "logs": "done",
    "error": None,
}
WEBHOOK_URL = "https://receiver.example.com/hook"
SECRET = "supersecretvalue123456"


def _add_outbox(job_id: str, *, attempts: int = 0) -> None:
    now = datetime.now(UTC)
    with SessionLocal() as db:
        db.add(
            Job(
                job_id=job_id,
                model="gemini-2.5-flash",
                url="https://example.com",
                status="success",
                result={"title": "Example"},
                webhook_url=WEBHOOK_URL,
            )
        )
        db.add(
            WebhookOutbox(
                job_id=job_id,
                event="job.completed",
                encrypted_secret=encrypt_webhook_secret(SECRET),
                attempts=attempts,
                next_attempt_at=now,
            )
        )
        db.commit()


def test_sign_payload_deterministic():
    expected = hmac.new(b"secret", b"hello", hashlib.sha256).hexdigest()
    assert sign_payload(b"hello", "secret") == expected


def test_build_body_includes_event_timestamp_and_payload():
    body = json.loads(_build_body("job.completed", PAYLOAD))
    assert body["event"] == "job.completed"
    assert "timestamp" in body
    assert body["job_id"] == "job-1"


def test_delivery_posts_signed_payload(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, content, headers):
            captured.update(url=url, content=content, headers=headers)
            return SimpleNamespace(status_code=200, headers={})

    monkeypatch.setattr(httpx, "Client", FakeClient)
    outcome = _deliver_once(
        payload=PAYLOAD,
        webhook_url=WEBHOOK_URL,
        webhook_secret=SECRET,
        event="job.completed",
    )
    assert outcome.delivered is True
    assert captured["headers"]["X-Webhook-Signature"] == (
        f"sha256={sign_payload(captured['content'], SECRET)}"
    )


def test_delivery_reports_connection_and_http_errors(monkeypatch):
    class BoomClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, content, headers):
            raise httpx.ConnectError("nope", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "Client", BoomClient)
    assert _deliver_once(
        payload=PAYLOAD,
        webhook_url=WEBHOOK_URL,
        webhook_secret=None,
        event="job.completed",
    ).error == "connection_error"

    class RateClient(BoomClient):
        def post(self, url, content, headers):
            return SimpleNamespace(status_code=429, headers={"Retry-After": "5"})

    monkeypatch.setattr(httpx, "Client", RateClient)
    outcome = _deliver_once(
        payload=PAYLOAD,
        webhook_url=WEBHOOK_URL,
        webhook_secret=None,
        event="job.completed",
    )
    assert outcome.error == "http_status:429"
    assert outcome.retry_after == 5


def test_retry_after_is_clamped():
    assert _parse_retry_after("9999") == 300


def test_publisher_sends_only_job_id(monkeypatch):
    _add_outbox("publish-me")
    calls = []

    def fake_apply_async(*, kwargs, task_id):
        calls.append((kwargs, task_id))

    monkeypatch.setattr(
        webhook, "deliver_job_webhook", SimpleNamespace(apply_async=fake_apply_async)
    )
    assert webhook.publish_pending_webhooks.run() == 1
    assert calls[0][0] == {"job_id": "publish-me"}
    assert calls[0][1] == "webhook:publish-me:1"


def test_publish_failure_releases_lease(monkeypatch):
    _add_outbox("publish-fails")

    def fail(**kwargs):
        raise RuntimeError("broker down")

    monkeypatch.setattr(
        webhook, "deliver_job_webhook", SimpleNamespace(apply_async=fail)
    )
    assert webhook.publish_pending_webhooks.run() == 0
    with SessionLocal() as db:
        row = db.get(WebhookOutbox, "publish-fails")
        assert row.published_at is None
        assert row.last_error == "publish_failed"


def test_publisher_recovers_expired_delivery_lease(monkeypatch):
    _add_outbox("expired-lease", attempts=1)
    now = datetime.now(UTC)
    with SessionLocal() as db:
        row = db.get(WebhookOutbox, "expired-lease")
        row.published_at = now - timedelta(minutes=2)
        row.delivery_started_at = now - timedelta(minutes=2)
        db.commit()

    calls = []

    def fake_apply_async(*, kwargs, task_id):
        calls.append((kwargs, task_id))

    monkeypatch.setattr(
        webhook, "deliver_job_webhook", SimpleNamespace(apply_async=fake_apply_async)
    )
    assert webhook.publish_pending_webhooks.run() == 1
    assert calls == [
        ({"job_id": "expired-lease"}, "webhook:expired-lease:2")
    ]


def test_delivery_success_updates_outbox_and_job(monkeypatch):
    _add_outbox("delivery-ok")
    monkeypatch.setattr(
        webhook, "_deliver_once", lambda **kwargs: DeliveryOutcome(True)
    )
    assert webhook.deliver_job_webhook.run(job_id="delivery-ok") == "delivered"
    with SessionLocal() as db:
        row = db.get(WebhookOutbox, "delivery-ok")
        job = db.get(Job, "delivery-ok")
        assert row.delivered_at is not None
        assert row.encrypted_secret is None
        assert job.webhook_delivered_at is not None
        assert job.webhook_error is None


def test_delivery_failure_persists_backoff(monkeypatch):
    _add_outbox("delivery-retry")
    monkeypatch.setattr(
        webhook,
        "_deliver_once",
        lambda **kwargs: DeliveryOutcome(False, "http_status:500"),
    )
    before = datetime.now(UTC)
    assert (
        webhook.deliver_job_webhook.run(job_id="delivery-retry")
        == "retry_scheduled"
    )
    with SessionLocal() as db:
        row = db.get(WebhookOutbox, "delivery-retry")
        assert row.attempts == 1
        assert row.published_at is None
        assert row.delivery_started_at is None
        assert row.last_error == "http_status:500"
        next_attempt = row.next_attempt_at.replace(tzinfo=UTC)
        assert next_attempt >= before + timedelta(seconds=2)


def test_delivery_exhaustion_is_terminal(monkeypatch):
    _add_outbox("delivery-exhausted", attempts=4)
    monkeypatch.setattr(
        webhook,
        "_deliver_once",
        lambda **kwargs: DeliveryOutcome(False, "http_status:500"),
    )
    assert (
        webhook.deliver_job_webhook.run(job_id="delivery-exhausted")
        == "exhausted"
    )
    with SessionLocal() as db:
        row = db.get(WebhookOutbox, "delivery-exhausted")
        assert row.attempts == 5
        assert row.exhausted_at is not None
        assert row.encrypted_secret is None


def test_delivered_duplicate_is_noop(monkeypatch):
    _add_outbox("already-delivered")
    with SessionLocal() as db:
        row = db.get(WebhookOutbox, "already-delivered")
        row.delivered_at = datetime.now(UTC)
        db.commit()

    def unexpected(**kwargs):
        raise AssertionError("webhook was delivered twice")

    monkeypatch.setattr(webhook, "_deliver_once", unexpected)
    assert (
        webhook.deliver_job_webhook.run(job_id="already-delivered")
        == "delivered"
    )
