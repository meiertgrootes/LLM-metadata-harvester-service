import hashlib
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

STATE_DIR = Path(os.environ.get("TEST_STATE_DIR", "/test-state"))
DELIVERIES = STATE_DIR / "deliveries.jsonl"
CALLS = STATE_DIR / "calls.jsonl"
SECRET = os.environ["WEBHOOK_RECEIVER_SECRET"]


def _read_json_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


class Handler(BaseHTTPRequestHandler):
    def _json_response(self, status: int, value: Any) -> None:
        body = json.dumps(value, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json_response(200, {"status": "ok"})
        elif self.path == "/state":
            self._json_response(
                200,
                {
                    "deliveries": _read_json_lines(DELIVERIES),
                    "harvester_calls": _read_json_lines(CALLS),
                },
            )
        else:
            self._json_response(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/webhook":
            self._json_response(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        signature = self.headers.get("X-Webhook-Signature", "")
        expected = "sha256=" + hmac.new(
            SECRET.encode(), body, hashlib.sha256
        ).hexdigest()
        delivery = {
            "payload": json.loads(body),
            "signature": signature,
            "signature_valid": hmac.compare_digest(signature, expected),
        }
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with DELIVERIES.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(delivery, sort_keys=True) + "\n")
        self._json_response(204, {})

    def log_message(self, format: str, *args: Any) -> None:
        return


if __name__ == "__main__":
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
