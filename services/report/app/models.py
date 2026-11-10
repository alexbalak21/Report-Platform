"""Tables of the `reports` schema (the service's DB role has `search_path = reports`).

report_counters is the atomic source of `yymmdd-N` numbers. A report pins
(template_id, template_version), so changing the template later never alters it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONType = JSON().with_variant(postgresql.JSONB(), "postgresql")

REPORT_STATUSES = ("draft", "final")
JOB_STATUSES = ("queued", "running", "done", "failed")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint("status in ('draft', 'final')", name="ck_reports_status"),
        UniqueConstraint("report_number", name="uq_reports_number"),
        # One report per record per template: a double click or two users racing get a 409.
        UniqueConstraint("template_id", "record_table", "record_id", name="uq_reports_record"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    report_number: Mapped[str] = mapped_column(String(40))
    report_date: Mapped[date] = mapped_column(Date)  # the day the number belongs to
    status: Mapped[str] = mapped_column(String(10), default="draft")
    template_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    template_version: Mapped[int] = mapped_column(Integer)
    template_name: Mapped[str] = mapped_column(String(120), default="")
    record_table: Mapped[str] = mapped_column(String(63))
    record_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    html_key: Mapped[str] = mapped_column(String(200))
    pdf_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Keywords that had no value when generated (shown as [[key?]] in the draft).
    unresolved_keys: Mapped[list[str]] = mapped_column(JSONType, default=list)
    # True once the number was written back to the source row in the data service.
    record_synced: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    finalized_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReportCounter(Base):
    __tablename__ = "report_counters"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    last_n: Mapped[int] = mapped_column(Integer, default=0)


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        CheckConstraint(
            "status in ('queued', 'running', 'done', 'failed')", name="ck_generation_jobs_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(10), default="queued")
    template_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    template_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    record_table: Mapped[str] = mapped_column(String(63))
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GenerationJobItem(Base):
    __tablename__ = "generation_job_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("generation_jobs.id", ondelete="CASCADE"), index=True
    )
    record_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    report_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="queued")  # queued|ok|failed
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
