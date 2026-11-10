"""blocks, block_versions

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "blocks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("slug", sa.String(64), nullable=True, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind in ('default', 'custom')", name="ck_blocks_kind"),
    )
    op.create_table(
        "block_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "block_id",
            sa.Uuid(),
            sa.ForeignKey("blocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", JSONType, nullable=False),
        sa.Column("html_sanitized", sa.Text(), nullable=False),
        sa.Column("css_sanitized", sa.Text(), nullable=False),
        sa.Column("scope_class", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("block_id", "version", name="uq_block_versions"),
    )
    op.create_index("ix_block_versions_block_id", "block_versions", ["block_id"])


def downgrade() -> None:
    op.drop_index("ix_block_versions_block_id", table_name="block_versions")
    op.drop_table("block_versions")
    op.drop_table("blocks")
