import os

# Database
# ========
# Results are persisted to PostgreSQL (SQLAlchemy). The Celery broker remains
# Redis; completed results are no longer stored in the result backend.

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://harvester:harvester@localhost:5432/harvester",
)


# Batch submission
# ================

BATCH_MAX_URLS = int(os.getenv("BATCH_MAX_URLS", "100"))


# Webhook delivery
# =================

WEBHOOK_TIMEOUT_SECONDS = float(os.getenv("WEBHOOK_TIMEOUT_SECONDS", "10"))
WEBHOOK_MAX_RETRIES = int(os.getenv("WEBHOOK_MAX_RETRIES", "5"))
WEBHOOK_RETRY_BACKOFF = float(os.getenv("WEBHOOK_RETRY_BACKOFF", "2"))
