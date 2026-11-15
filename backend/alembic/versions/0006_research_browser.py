"""Persist the supplementary research stream and source details."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_sources",
        sa.Column("task_id", sa.Uuid(), primary_key=True),
        sa.Column("source_id", sa.Uuid(), primary_key=True),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("fetch_attempt", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["research_tasks.task_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.evidence_id"], ondelete="SET NULL"),
        sa.UniqueConstraint("task_id", "evidence_id", name="uq_research_source_evidence"),
    )
    op.create_table(
        "research_events",
        sa.Column("task_id", sa.Uuid(), primary_key=True),
        sa.Column("sequence", sa.BigInteger(), primary_key=True),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id", "trace_id"],
            ["research_tasks.task_id", "research_tasks.trace_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "sequence BETWEEN 1 AND 9007199254740991", name="ck_research_events_seq"
        ),
    )


def downgrade() -> None:
    op.drop_table("research_events")
    op.drop_table("research_sources")
