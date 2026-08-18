from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from llm_metadata_harvester_service.db.session import Base
from llm_metadata_harvester_service.db.status import JobStatus


class Job(Base):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    model: Mapped[str] = mapped_column(String(128))
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), index=True, default=JobStatus.QUEUED
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    logs: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_attempts: Mapped[int] = mapped_column(Integer, default=0)
    webhook_last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    webhook_delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    webhook_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    execution_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'pending', 'success', 'failure')",
            name="ck_jobs_status",
        ),
        Index("ix_jobs_batch_id_status", "batch_id", "status"),
        Index("ix_jobs_status_updated_at", "status", "updated_at"),
    )
