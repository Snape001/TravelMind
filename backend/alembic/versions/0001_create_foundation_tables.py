"""create foundation tables

Revision ID: 0001
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_tasks",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("brief", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="queued", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'needs_clarification', 'paused', "
            "'completed', 'failed')",
            name="ck_research_tasks_status",
        ),
        sa.PrimaryKeyConstraint("task_id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("task_id", "trace_id", name="uq_research_tasks_task_trace"),
        sa.UniqueConstraint("trace_id"),
    )
    op.create_table(
        "agent_events",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("sequence BETWEEN 1 AND 9007199254740991", name="ck_agent_events_seq"),
        sa.CheckConstraint("version = 1", name="ck_agent_events_protocol"),
        sa.ForeignKeyConstraint(
            ["task_id", "trace_id"],
            ["research_tasks.task_id", "research_tasks.trace_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id", "sequence"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')", name="ck_agent_runs_status"
        ),
        sa.ForeignKeyConstraint(["task_id"], ["research_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index(op.f("ix_agent_runs_task_id"), "agent_runs", ["task_id"], unique=False)
    op.create_table(
        "evidence",
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_title", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_locator", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('web', 'official', 'academic', 'book', 'social', 'map')",
            name="ck_evidence_source_type",
        ),
        sa.CheckConstraint(
            "source_url IS NOT NULL OR source_locator IS NOT NULL", name="ck_evidence_reference"
        ),
        sa.ForeignKeyConstraint(["task_id"], ["research_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("evidence_id"),
    )
    op.create_index(op.f("ix_evidence_task_id"), "evidence", ["task_id"], unique=False)
    op.create_table(
        "reports",
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version >= 1", name="ck_reports_version"),
        sa.ForeignKeyConstraint(["task_id"], ["research_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("report_id"),
        sa.UniqueConstraint("task_id", "version", name="uq_reports_task_version"),
    )


def downgrade() -> None:
    op.drop_table("reports")
    op.drop_index(op.f("ix_evidence_task_id"), table_name="evidence")
    op.drop_table("evidence")
    op.drop_index(op.f("ix_agent_runs_task_id"), table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_table("agent_events")
    op.drop_table("research_tasks")
