"""Tables of the `templates` schema. The service's DB role has `search_path = templates`, so no
schema is hard-coded here (keeps the models portable to SQLite for tests).

A template has ONE mutable draft (autosaved by the builder, guarded by `draft_revision`) and any
number of immutable published versions. Reports pin (template_id, version).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(postgresql.JSONB(), "postgresql")

STATUSES = ("draft", "published", "archived")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Template(Base):
    __tablename__ = "templates"
    __table_args__ = (
        CheckConstraint("status in ('draft', 'published', 'archived')", name="ck_templates_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    # draft = never published, published = has a published version, archived = hidden.
    status: Mapped[str] = mapped_column(String(10), default="draft")
    draft_document: Mapped[dict[str, Any]] = mapped_column(JSONType)
    draft_revision: Mapped[int] = mapped_column(Integer, default=1)
    draft_hash: Mapped[str] = mapped_column(String(64))
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    published_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Blocks pinned by the draft that have a newer version, kept by the block.updated consumer:
    # [{"block_id", "name", "pinned", "latest"}]
    outdated_blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    versions: Mapped[list[TemplateVersion]] = relationship(
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="TemplateVersion.version",
    )


class TemplateVersion(Base):
    """Immutable published snapshot."""

    __tablename__ = "template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version", name="uq_template_versions"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    template_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("templates.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    document: Mapped[dict[str, Any]] = mapped_column(JSONType)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    template: Mapped[Template] = relationship(back_populates="versions")


class TemplateBlockUsage(Base):
    """Which block versions a template's draft / published document pins (rebuilt on every
    save and publish). Lets the block.updated consumer find affected templates without
    scanning documents."""

    __tablename__ = "template_block_usage"
    __table_args__ = (
        CheckConstraint("source in ('draft', 'published')", name="ck_template_block_usage_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    template_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("templates.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(9))
    block_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    block_version: Mapped[int] = mapped_column(Integer)
