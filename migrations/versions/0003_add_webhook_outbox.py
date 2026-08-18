"""Add durable webhook outbox.

Revision ID: 0003
Revises: 0002
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs", sa.Column("webhook_secret_encrypted", sa.Text(), nullable=True)
    )
    op.create_table(
        "webhook_outbox",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("event", sa.String(length=32), nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exhausted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index(
        "ix_webhook_outbox_due",
        "webhook_outbox",
        ["delivered_at", "exhausted_at", "next_attempt_at", "published_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_outbox_due", table_name="webhook_outbox")
    op.drop_table("webhook_outbox")
    op.drop_column("jobs", "webhook_secret_encrypted")
