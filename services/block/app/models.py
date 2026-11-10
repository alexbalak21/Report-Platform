"""Tables of the `blocks` schema. The block service's DB role has `search_path = blocks`, so no
schema is hard-coded here (keeps the models portable to SQLite for tests)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
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

KINDS = ("default", "custom")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Block(Base):
    __tablename__ = "blocks"
    __table_args__ = (CheckConstraint("kind in ('default', 'custom')", name="ck_blocks_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(8), default="custom")
    # Stable identifier of a shipped default block (None for custom blocks).
    slug: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(32), default="other")
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    versions: Mapped[list[BlockVersion]] = relationship(
        back_populates="block", cascade="all, delete-orphan", order_by="BlockVersion.version"
    )


class BlockVersion(Base):
    """Immutable snapshot. Templates pin (block_id, version), so a row is never edited."""

    __tablename__ = "block_versions"
    __table_args__ = (UniqueConstraint("block_id", "version", name="uq_block_versions"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    block_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("blocks.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    # What the author wrote (validated) + computed `placeholders` / `loops`.
    definition: Mapped[dict[str, Any]] = mapped_column(JSONType)
    html_sanitized: Mapped[str] = mapped_column(Text)
    css_sanitized: Mapped[str] = mapped_column(Text, default="")  # already scoped
    scope_class: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    block: Mapped[Block] = relationship(back_populates="versions")
