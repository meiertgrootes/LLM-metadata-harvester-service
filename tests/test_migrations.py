from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.schema import CreateTable

from llm_metadata_harvester_service.db.models import Job
from llm_metadata_harvester_service.db.session import engine


def test_migration_created_jobs_schema():
    inspector = inspect(engine)
    assert "jobs" in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("jobs")}
    assert "execution_attempts" in columns
    assert {
        "ix_jobs_batch_id",
        "ix_jobs_status",
        "ix_jobs_batch_id_status",
        "ix_jobs_status_updated_at",
    } <= {index["name"] for index in inspector.get_indexes("jobs")}
    assert "ck_jobs_status" in {
        constraint["name"] for constraint in inspector.get_check_constraints("jobs")
    }
    assert "execution_attempts" in str(CreateTable(Job.__table__).compile(engine))


def test_legacy_schema_upgrades_from_baseline(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path}/legacy.db"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config("alembic.ini")
    command.upgrade(config, "0001")
    legacy = inspect(create_engine(database_url))
    assert "execution_attempts" not in {
        column["name"] for column in legacy.get_columns("jobs")
    }

    command.upgrade(config, "head")
    current = inspect(create_engine(database_url))
    assert "execution_attempts" in {
        column["name"] for column in current.get_columns("jobs")
    }
