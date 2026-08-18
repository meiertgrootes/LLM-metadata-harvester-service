"""Create persisted jobs table.

Revision ID: 0001
Revises:
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("logs", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("webhook_url", sa.Text(), nullable=True),
        sa.Column("webhook_attempts", sa.Integer(), nullable=False),
        sa.Column("webhook_last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("webhook_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("webhook_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index("ix_jobs_batch_id", "jobs", ["batch_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_batch_id_status", "jobs", ["batch_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_jobs_batch_id_status", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_batch_id", table_name="jobs")
    op.drop_table("jobs")
