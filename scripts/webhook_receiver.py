#!/usr/bin/env python
# src-free dev helper: run with `uvicorn webhook_receiver:app --port 8080`
# or `python webhook_receiver.py`. Prints incoming webhook deliveries.

import hashlib
import hmac
import json
import os

import uvicorn
from fastapi import FastAPI, Request

app = FastAPI(title="Webhook receiver (dev)")

# Set WEBHOOK_RECEIVER_SECRET to the same value as `webhook_secret` used at
# job submission to enable signature verification.
WEBHOOK_RECEIVER_SECRET = os.getenv("WEBHOOK_RECEIVER_SECRET")


def _verify_signature(body: bytes, signature: str) -> bool:
    if WEBHOOK_RECEIVER_SECRET is None:
        return True
    expected = hmac.new(
        WEBHOOK_RECEIVER_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, f"sha256={expected}")


@app.post("/")
async def receive(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")

    if not _verify_signature(body, signature):
        print("SIGNATURE VERIFICATION FAILED")
        return {"status": "invalid_signature"}

    payload = json.loads(body)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return {"status": "ok"}


@app.get("/")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
