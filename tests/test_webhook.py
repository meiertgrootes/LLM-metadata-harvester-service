import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from celery.exceptions import Retry

from llm_metadata_harvester_service.db.models import Job
from llm_metadata_harvester_service.db.session import SessionLocal
from llm_metadata_harvester_service.workers.webhook import (
    _build_body,
    _deliver,
    _parse_retry_after,
    _record_attempt,
    _record_delivered,
    _record_failed,
    sign_payload,
)

PAYLOAD = {
    "job_id": "job-1",
    "status": "success",
    "model": "gemini-2.5-flash",
    "result": {"title": "Example"},
    "logs": "extracting...\n",
    "error": None,
}
WEBHOOK_URL = "https://receiver.example.com/hook"
SECRET = "supersecretvalue123456"


def _fake_self(retries=0, max_retries=5):
    retry = MagicMock(side_effect=Retry())
    request = SimpleNamespace(retries=retries)
    return SimpleNamespace(request=request, max_retries=max_retries, retry=retry)


def test_sign_payload_deterministic():
    expected = hmac.new(b"secret", b"hello", hashlib.sha256).hexdigest()
    assert sign_payload(b"hello", "secret") == expected
    assert sign_payload(b"hello", "secret") == sign_payload(b"hello", "secret")


def test_build_body_includes_event_timestamp_and_payload():
    body = json.loads(_build_body("job.completed", PAYLOAD))
    assert body["event"] == "job.completed"
    assert "timestamp" in body
    assert body["job_id"] == "job-1"
    assert body["status"] == "success"
    assert body["result"] == {"title": "Example"}


def test_dispatch_success_posts_signed_payload(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, timeout, follow_redirects=False, **kwargs):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, content, headers):
            captured["url"] = url
            captured["content"] = content
            captured["headers"] = headers
            return SimpleNamespace(status_code=200, headers={})

    monkeypatch.setattr(httpx, "Client", FakeClient)

    delivered = _deliver(
        _fake_self(),
        payload=PAYLOAD,
        webhook_url=WEBHOOK_URL,
        webhook_secret=SECRET,
        event="job.completed",
    )

    assert delivered.delivered is True
    assert captured["url"] == WEBHOOK_URL
    assert captured["timeout"] == 10.0
    assert captured["headers"]["X-Webhook-Signature"] == (
        f"sha256={sign_payload(captured['content'], SECRET)}"
    )
    body = json.loads(captured["content"])
    assert body["event"] == "job.completed"
    assert body["job_id"] == "job-1"


def test_dispatch_without_secret_sets_no_signature(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, timeout, follow_redirects=False, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, content, headers):
            captured["headers"] = headers
            return SimpleNamespace(status_code=200, headers={})

    monkeypatch.setattr(httpx, "Client", FakeClient)

    delivered = _deliver(
        _fake_self(),
        payload=PAYLOAD,
        webhook_url=WEBHOOK_URL,
        webhook_secret=None,
        event="job.completed",
    )

    assert delivered.delivered is True
    assert "X-Webhook-Signature" not in captured["headers"]


def test_dispatch_retries_on_connection_error(monkeypatch):
    class BoomClient:
        def __init__(self, timeout, follow_redirects=False, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, content, headers):
            raise httpx.ConnectError("connection refused", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "Client", BoomClient)

    self = _fake_self(retries=0, max_retries=5)
    with pytest.raises(Retry):
        _deliver(
            self,
            payload=PAYLOAD,
            webhook_url=WEBHOOK_URL,
            webhook_secret=SECRET,
            event="job.completed",
        )
    self.retry.assert_called_once()
    assert "exc" in self.retry.call_args.kwargs
    assert self.retry.call_args.kwargs["countdown"] == 2.0


def test_dispatch_gives_up_after_final_connection_error(monkeypatch):
    class BoomClient:
        def __init__(self, timeout, follow_redirects=False, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, content, headers):
            raise httpx.ConnectError("connection refused", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "Client", BoomClient)

    self = _fake_self(retries=5, max_retries=5)
    delivered = _deliver(
        self,
        payload=PAYLOAD,
        webhook_url=WEBHOOK_URL,
        webhook_secret=SECRET,
        event="job.completed",
    )
    assert delivered.delivered is False
    assert delivered.error == "connection_error"
    self.retry.assert_not_called()


def test_dispatch_retries_on_non_2xx(monkeypatch):
    response = SimpleNamespace(
        status_code=500,
        headers={},
        request=httpx.Request("POST", WEBHOOK_URL),
    )

    class FiveClient:
        def __init__(self, timeout, follow_redirects=False, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, content, headers):
            return response

    monkeypatch.setattr(httpx, "Client", FiveClient)

    self = _fake_self(retries=0, max_retries=5)
    with pytest.raises(Retry):
        _deliver(
            self,
            payload=PAYLOAD,
            webhook_url=WEBHOOK_URL,
            webhook_secret=SECRET,
            event="job.completed",
        )
    self.retry.assert_called_once()
    assert self.retry.call_args.kwargs["countdown"] == 2.0


def test_dispatch_retries_on_redirect(monkeypatch):
    response = SimpleNamespace(
        status_code=302,
        headers={"Location": "https://elsewhere.example/hook"},
        request=httpx.Request("POST", WEBHOOK_URL),
    )

    class RedirectClient:
        def __init__(self, timeout, follow_redirects=False, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, content, headers):
            return response

    monkeypatch.setattr(httpx, "Client", RedirectClient)
    self = _fake_self(retries=0, max_retries=4)
    with pytest.raises(Retry):
        _deliver(
            self,
            payload=PAYLOAD,
            webhook_url=WEBHOOK_URL,
            webhook_secret=SECRET,
            event="job.completed",
        )


def test_dispatch_honours_retry_after(monkeypatch):
    response = SimpleNamespace(
        status_code=429,
        headers={"Retry-After": "5"},
        request=httpx.Request("POST", WEBHOOK_URL),
    )

    class RateClient:
        def __init__(self, timeout, follow_redirects=False, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, content, headers):
            return response

    monkeypatch.setattr(httpx, "Client", RateClient)

    self = _fake_self(retries=0, max_retries=5)
    with pytest.raises(Retry):
        _deliver(
            self,
            payload=PAYLOAD,
            webhook_url=WEBHOOK_URL,
            webhook_secret=SECRET,
            event="job.completed",
        )
    assert self.retry.call_args.kwargs["countdown"] == 5.0


def test_retry_backoff_is_exponential(monkeypatch):
    class BoomClient:
        def __init__(self, timeout, follow_redirects=False, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, content, headers):
            raise httpx.ConnectError("nope", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "Client", BoomClient)
    self = _fake_self(retries=3, max_retries=4)
    with pytest.raises(Retry):
        _deliver(
            self,
            payload=PAYLOAD,
            webhook_url=WEBHOOK_URL,
            webhook_secret=SECRET,
            event="job.completed",
        )
    assert self.retry.call_args.kwargs["countdown"] == 16.0


def test_retry_after_is_clamped():
    assert _parse_retry_after("9999", 2.0) == 300.0


def test_dispatch_gives_up_after_final_non_2xx(monkeypatch):
    response = SimpleNamespace(
        status_code=500,
        headers={},
        request=httpx.Request("POST", WEBHOOK_URL),
    )

    class FiveClient:
        def __init__(self, timeout, follow_redirects=False, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, content, headers):
            return response

    monkeypatch.setattr(httpx, "Client", FiveClient)

    self = _fake_self(retries=5, max_retries=5)
    delivered = _deliver(
        self,
        payload=PAYLOAD,
        webhook_url=WEBHOOK_URL,
        webhook_secret=SECRET,
        event="job.completed",
    )
    assert delivered.delivered is False
    assert delivered.error == "http_status:500"
    self.retry.assert_not_called()


def test_dispatch_rejects_unsupported_scheme():
    with pytest.raises(ValueError):
        _deliver(
            _fake_self(),
            payload=PAYLOAD,
            webhook_url="ftp://receiver.example.com/hook",
            webhook_secret=None,
            event="job.completed",
        )


def test_record_delivery_metadata():
    job_id = "job-meta"
    with SessionLocal() as db:
        db.add(Job(job_id=job_id, model="m", url="https://x.example", status="queued"))
        db.commit()

    _record_attempt(job_id)
    _record_attempt(job_id)
    _record_delivered(job_id)

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        assert job.webhook_attempts == 2
        assert job.webhook_last_attempt_at is not None
        assert job.webhook_delivered_at is not None
        assert job.webhook_error is None

    _record_failed(job_id, "delivery failed permanently")

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        assert job.webhook_error is None


def test_record_delivery_metadata_missing_job_is_noop():
    _record_attempt("nope")
    _record_delivered("nope")
    _record_failed("nope", "boom")


def test_failed_duplicate_does_not_override_delivered_outcome():
    job_id = "job-delivered"
    with SessionLocal() as db:
        db.add(Job(job_id=job_id, model="m", url="https://x.example", status="success"))
        db.commit()

    _record_delivered(job_id)
    _record_failed(job_id, "http_status:500")

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        assert job.webhook_delivered_at is not None
        assert job.webhook_error is None
