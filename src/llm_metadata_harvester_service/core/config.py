import os

# Webhook delivery
# =================

WEBHOOK_TIMEOUT_SECONDS = float(os.getenv("WEBHOOK_TIMEOUT_SECONDS", "10"))
WEBHOOK_MAX_RETRIES = int(os.getenv("WEBHOOK_MAX_RETRIES", "5"))
WEBHOOK_RETRY_BACKOFF = float(os.getenv("WEBHOOK_RETRY_BACKOFF", "2"))


# Result retention
# ================
# How long completed job results remain retrievable from the result backend
# (seconds). Extended to 24h so results survive webhook delivery retries and
# remain pollable as a fallback.

RESULT_EXPIRES_SECONDS = int(os.getenv("RESULT_EXPIRES_SECONDS", "86400"))
