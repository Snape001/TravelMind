"""Persist the execution budget and each epoch's execution permission."""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_tasks",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column("research_tasks", sa.Column("lease_execution_permitted", sa.Boolean()))

    # Older versions counted every reclaim, so existing counts can exceed the default.
    # A queued retry already promised by the old process retains one executable attempt.
    op.execute(
        """UPDATE research_tasks SET
            max_attempts = GREATEST(max_attempts, attempt_count + 1)
        WHERE status = 'queued' OR (status = 'paused' AND paused_interrupt_id IS NULL)"""
    )
    op.execute(
        """UPDATE research_tasks SET
            attempt_count = LEAST(attempt_count, max_attempts),
            lease_execution_permitted = CASE WHEN lease_worker_id IS NOT NULL THEN false END,
            lease_until = CASE WHEN lease_until IS NOT NULL
                THEN LEAST(lease_until, statement_timestamp()) END"""
    )
    op.create_check_constraint(
        "ck_research_tasks_attempt_budget",
        "research_tasks",
        "max_attempts >= 1 AND attempt_count <= max_attempts",
    )
    op.create_check_constraint(
        "ck_research_tasks_lease_permission",
        "research_tasks",
        "(lease_worker_id IS NULL) = (lease_execution_permitted IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_research_tasks_lease_permission", "research_tasks", type_="check")
    op.drop_constraint("ck_research_tasks_attempt_budget", "research_tasks", type_="check")
    op.drop_column("research_tasks", "lease_execution_permitted")
    op.drop_column("research_tasks", "max_attempts")
