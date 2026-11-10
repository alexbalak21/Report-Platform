"""templates, template_versions, template_block_usage

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
        "templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("draft_document", JSONType, nullable=False),
        sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column("draft_hash", sa.String(64), nullable=False),
        sa.Column("published_version", sa.Integer(), nullable=True),
        sa.Column("published_hash", sa.String(64), nullable=True),
        sa.Column("outdated_blocks", JSONType, nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('draft', 'published', 'archived')", name="ck_templates_status"
        ),
    )
    op.create_table(
        "template_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "template_id",
            sa.Uuid(),
            sa.ForeignKey("templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("document", JSONType, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("template_id", "version", name="uq_template_versions"),
    )
    op.create_index("ix_template_versions_template_id", "template_versions", ["template_id"])
    op.create_table(
        "template_block_usage",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "template_id",
            sa.Uuid(),
            sa.ForeignKey("templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(9), nullable=False),
        sa.Column("block_id", sa.Uuid(), nullable=False),
        sa.Column("block_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "source in ('draft', 'published')", name="ck_template_block_usage_source"
        ),
    )
    op.create_index("ix_template_block_usage_template_id", "template_block_usage", ["template_id"])
    op.create_index("ix_template_block_usage_block_id", "template_block_usage", ["block_id"])


def downgrade() -> None:
    op.drop_index("ix_template_block_usage_block_id", table_name="template_block_usage")
    op.drop_index("ix_template_block_usage_template_id", table_name="template_block_usage")
    op.drop_table("template_block_usage")
    op.drop_index("ix_template_versions_template_id", table_name="template_versions")
    op.drop_table("template_versions")
    op.drop_table("templates")
