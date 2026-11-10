"""reports, report_counters, generation_jobs, generation_job_items

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
        "reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("report_number", sa.String(40), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("template_name", sa.String(120), nullable=False),
        sa.Column("record_table", sa.String(63), nullable=False),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("html_key", sa.String(200), nullable=False),
        sa.Column("pdf_key", sa.String(200), nullable=True),
        sa.Column("unresolved_keys", JSONType, nullable=False),
        sa.Column("record_synced", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_by", sa.Uuid(), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status in ('draft', 'final')", name="ck_reports_status"),
        sa.UniqueConstraint("report_number", name="uq_reports_number"),
        sa.UniqueConstraint("template_id", "record_table", "record_id", name="uq_reports_record"),
    )
    op.create_index("ix_reports_template_id", "reports", ["template_id"])
    op.create_index("ix_reports_record", "reports", ["record_table", "record_id"])
    op.create_table(
        "report_counters",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("last_n", sa.Integer(), nullable=False),
    )
    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=True),
        sa.Column("record_table", sa.String(63), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("done", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'done', 'failed')", name="ck_generation_jobs_status"
        ),
    )
    op.create_table(
        "generation_job_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("generation_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("error", JSONType, nullable=True),
    )
    op.create_index("ix_generation_job_items_job_id", "generation_job_items", ["job_id"])


def downgrade() -> None:
    op.drop_table("generation_job_items")
    op.drop_table("generation_jobs")
    op.drop_table("report_counters")
    op.drop_table("reports")
