import ipaddress
import socket
from urllib.parse import urlsplit

from llm_metadata_harvester_service.core.config import WEBHOOK_ALLOWED_HOSTS


class WebhookURLValidationError(ValueError):
    pass


def validate_webhook_url(url: str) -> tuple[str, ...] | None:
    """Reject webhook destinations that could access non-public services."""
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise WebhookURLValidationError("Webhook URL must use HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise WebhookURLValidationError("Webhook URL must not include user information")

    host = parsed.hostname
    if host is None:
        raise WebhookURLValidationError("Webhook URL must include a host")
    normalized_host = host.lower().rstrip(".")
    if normalized_host in WEBHOOK_ALLOWED_HOSTS:
        return None

    try:
        addresses = {
            str(entry[4][0])
            for entry in socket.getaddrinfo(
                normalized_host,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        }
    except socket.gaierror as exc:
        raise WebhookURLValidationError("Webhook host could not be resolved") from exc

    if not addresses:
        raise WebhookURLValidationError("Webhook host could not be resolved")

    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise WebhookURLValidationError("Webhook host resolved unexpectedly") from exc
        if not ip.is_global:
            raise WebhookURLValidationError(
                "Webhook URL must resolve only to public IP addresses"
            )
    return tuple(sorted(addresses))
