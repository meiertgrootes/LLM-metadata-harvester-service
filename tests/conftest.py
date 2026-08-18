import os
import sys
import tempfile
import types
from pathlib import Path

# Configure the database and broker BEFORE importing the application, so the
# engine is created against a throwaway SQLite file rather than PostgreSQL.
_TMPDIR = tempfile.mkdtemp(prefix="llm-harvester-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMPDIR}/test.db"
os.environ["CELERY_BROKER_URL"] = "memory://"
os.environ["WEBHOOK_ALLOWED_HOSTS"] = "receiver.example.com"
os.environ["WEBHOOK_SECRET_KEY"] = "o1pxvABKy2_-zKRx2m60YhBvsRqGXTNgSbh7wAwmOPY="

SRC = Path(__file__).resolve().parent.parent / "src"
ROOT = SRC.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Stub the external llm-metadata-harvester package so unit tests can import
# the worker task without the upstream dependency being installed. If the real
# package is present, it is left untouched.
async def _metadata_harvest(*, model_name: str, url: str, api_key: str):
    return {"mock": "metadata"}


harvester = types.ModuleType("llm_metadata_harvester")
harvester_ops = types.ModuleType("llm_metadata_harvester.harvester_operations")
harvester_ops.metadata_harvest = _metadata_harvest
harvester.harvester_operations = harvester_ops

sys.modules.setdefault("llm_metadata_harvester", harvester)
sys.modules.setdefault("llm_metadata_harvester.harvester_operations", harvester_ops)

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from llm_metadata_harvester_service.db.session import Base, SessionLocal  # noqa: E402

command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    with SessionLocal() as db:
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(table.delete())
        db.commit()
