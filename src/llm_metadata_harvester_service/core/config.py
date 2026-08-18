import os


def _get_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))

# Database
# ========
# Results are persisted to PostgreSQL (SQLAlchemy). The Celery broker remains
# Redis; completed results are no longer stored in the result backend.

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://harvester:harvester@localhost:5432/harvester",
)

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
WEBHOOK_SECRET_KEY = os.getenv("WEBHOOK_SECRET_KEY")

CELERY_TASK_SOFT_TIME_LIMIT_SECONDS = _get_int(
    "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS", 3300
)
CELERY_TASK_TIME_LIMIT_SECONDS = _get_int("CELERY_TASK_TIME_LIMIT_SECONDS", 3600)
JOB_PENDING_STALE_SECONDS = _get_int("JOB_PENDING_STALE_SECONDS", 4500)
JOB_QUEUED_STALE_SECONDS = _get_int("JOB_QUEUED_STALE_SECONDS", 86400)
JOB_RECONCILE_INTERVAL_SECONDS = _get_int("JOB_RECONCILE_INTERVAL_SECONDS", 60)


# Batch submission
# ================

BATCH_MAX_URLS = _get_int("BATCH_MAX_URLS", 100)


# Webhook delivery
# =================

WEBHOOK_TIMEOUT_SECONDS = float(os.getenv("WEBHOOK_TIMEOUT_SECONDS", "10"))
WEBHOOK_MAX_ATTEMPTS = _get_int("WEBHOOK_MAX_ATTEMPTS", 5)
WEBHOOK_RETRY_BACKOFF = float(os.getenv("WEBHOOK_RETRY_BACKOFF", "2"))
WEBHOOK_RETRY_AFTER_MAX_SECONDS = float(
    os.getenv("WEBHOOK_RETRY_AFTER_MAX_SECONDS", "300")
)
WEBHOOK_OUTBOX_INTERVAL_SECONDS = _get_int("WEBHOOK_OUTBOX_INTERVAL_SECONDS", 5)
WEBHOOK_OUTBOX_LEASE_SECONDS = _get_int("WEBHOOK_OUTBOX_LEASE_SECONDS", 60)
WEBHOOK_ALLOWED_HOSTS = frozenset(
    host.strip().lower().rstrip(".")
    for host in os.getenv("WEBHOOK_ALLOWED_HOSTS", "").split(",")
    if host.strip()
)


if not (
    0
    < CELERY_TASK_SOFT_TIME_LIMIT_SECONDS
    < CELERY_TASK_TIME_LIMIT_SECONDS
    < JOB_PENDING_STALE_SECONDS
):
    raise ValueError(
        "Expected soft time limit < hard time limit < pending stale timeout"
    )

if WEBHOOK_MAX_ATTEMPTS < 1:
    raise ValueError("WEBHOOK_MAX_ATTEMPTS must be at least 1")
