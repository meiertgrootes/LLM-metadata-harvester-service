import hashlib
import hmac
import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

SCRIPT = Path(__file__).parents[1] / "scripts" / "webhook_receiver.py"
SPEC = importlib.util.spec_from_file_location("webhook_receiver", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
webhook_receiver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(webhook_receiver)

client = TestClient(webhook_receiver.app)
SECRET = "1234567890abcdef"


def _signature(body: bytes) -> str:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_health_endpoint():
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_valid_signature_is_checked_against_exact_request_bytes(monkeypatch):
    monkeypatch.setattr(webhook_receiver, "WEBHOOK_RECEIVER_SECRET", SECRET)
    body = b'{"job_id":"job-1", "status":"success"}'
    captured = []
    verify_signature = webhook_receiver._verify_signature

    def capture_body(value, signature):
        captured.append(value)
        return verify_signature(value, signature)

    monkeypatch.setattr(webhook_receiver, "_verify_signature", capture_body)
    response = client.post(
        "/",
        content=body,
        headers={"X-Webhook-Signature": _signature(body)},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert captured == [body]

    changed_body = body.replace(b", ", b",")
    response = client.post(
        "/",
        content=changed_body,
        headers={"X-Webhook-Signature": _signature(body)},
    )
    assert response.status_code == 401


def test_invalid_or_missing_signature_is_rejected(monkeypatch):
    monkeypatch.setattr(webhook_receiver, "WEBHOOK_RECEIVER_SECRET", SECRET)
    body = b'{"job_id":"job-1"}'

    invalid = client.post(
        "/",
        content=body,
        headers={"X-Webhook-Signature": "sha256=invalid"},
    )
    missing = client.post("/", content=body)

    assert invalid.status_code == 401
    assert invalid.json() == {"detail": "invalid_signature"}
    assert missing.status_code == 401
    assert missing.json() == {"detail": "invalid_signature"}
