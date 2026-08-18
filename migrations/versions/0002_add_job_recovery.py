"""Add job recovery state and constraints.

Revision ID: 0002
Revises: 0001
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "execution_attempts",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            "ck_jobs_status",
            "status IN ('queued', 'pending', 'success', 'failure')",
        )
        batch_op.create_index(
            "ix_jobs_status_updated_at", ["status", "updated_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_index("ix_jobs_status_updated_at")
        batch_op.drop_constraint("ck_jobs_status", type_="check")
        batch_op.drop_column("execution_attempts")
