import socket

import pytest

from llm_metadata_harvester_service.core import webhook_security
from llm_metadata_harvester_service.core.webhook_security import (
    WebhookURLValidationError,
    validate_webhook_url,
)


def _dns_result(address: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"],
)
def test_rejects_non_public_resolved_addresses(monkeypatch, address):
    monkeypatch.setattr(
        webhook_security.socket,
        "getaddrinfo",
        lambda *args, **kwargs: _dns_result(address),
    )
    with pytest.raises(WebhookURLValidationError):
        validate_webhook_url("https://unsafe.example/hook")


def test_accepts_public_resolved_address(monkeypatch):
    monkeypatch.setattr(
        webhook_security.socket,
        "getaddrinfo",
        lambda *args, **kwargs: _dns_result("93.184.216.34"),
    )
    assert validate_webhook_url("https://safe.example/hook") == ("93.184.216.34",)


def test_rejects_user_information(monkeypatch):
    with pytest.raises(WebhookURLValidationError):
        validate_webhook_url("https://user:password@safe.example/hook")


def test_pinned_transport_uses_validated_address(monkeypatch):
    from llm_metadata_harvester_service.workers.webhook import _PinnedNetworkBackend

    calls = []

    def fake_connect(self, host, port, **kwargs):
        calls.append((host, port))
        return object()

    monkeypatch.setattr(
        webhook_security.socket,
        "getaddrinfo",
        lambda *args, **kwargs: _dns_result("93.184.216.34"),
    )
    monkeypatch.setattr(
        "httpcore.SyncBackend.connect_tcp",
        fake_connect,
    )
    addresses = validate_webhook_url("https://safe.example/hook")
    assert addresses is not None
    _PinnedNetworkBackend("safe.example", addresses).connect_tcp(
        "safe.example", 443
    )
    assert calls == [("93.184.216.34", 443)]
