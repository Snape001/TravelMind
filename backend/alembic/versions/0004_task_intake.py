"""Persist destination intake and questionnaire snapshots."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_tasks",
        sa.Column("create_operation", sa.String(24), nullable=False, server_default="create_task"),
    )
    op.drop_constraint("uq_research_tasks_owner_key", "research_tasks", type_="unique")
    op.create_unique_constraint(
        "uq_research_tasks_owner_key",
        "research_tasks",
        ["owner_id", "create_operation", "idempotency_key"],
    )
    op.drop_constraint("ck_task_commands_operation", "task_commands", type_="check")
    op.alter_column("task_commands", "operation", type_=sa.String(24))
    op.create_check_constraint(
        "ck_task_commands_operation",
        "task_commands",
        "operation IN ('pause', 'resume', 'answer', 'answer_questionnaire')",
    )
    op.create_table(
        "task_intake",
        sa.Column("task_id", sa.Uuid(), primary_key=True),
        sa.Column("original_destination", sa.String(80), nullable=False),
        sa.Column("search_text", sa.String(80), nullable=False),
        sa.Column("destination", postgresql.JSONB()),
        sa.Column("planner_context", postgresql.JSONB()),
        sa.ForeignKeyConstraint(["task_id"], ["research_tasks.task_id"], ondelete="CASCADE"),
    )
    op.create_table(
        "task_questionnaires",
        sa.Column("task_id", sa.Uuid(), primary_key=True),
        sa.Column("questionnaire_id", sa.Uuid(), primary_key=True),
        sa.Column("questionnaire", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id", "questionnaire_id"],
            ["task_clarifications.task_id", "task_clarifications.clarification_id"],
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    # Older workers cannot recover the structured context of unfinished typed tasks.
    op.execute(
        """
        WITH ended AS (
            UPDATE research_tasks SET status = 'failed', updated_at = clock_timestamp(),
                available_at = NULL, lease_worker_id = NULL, lease_until = NULL,
                lease_execution_permitted = NULL, pause_requested_at = NULL,
                paused_interrupt_id = NULL
            FROM task_intake
            WHERE research_tasks.task_id = task_intake.task_id
              AND research_tasks.status NOT IN ('completed', 'failed')
            RETURNING research_tasks.task_id, trace_id, updated_at
        )
        INSERT INTO agent_events (task_id, trace_id, sequence, event_id, occurred_at, payload)
        SELECT ended.task_id, ended.trace_id,
            COALESCE((SELECT max(sequence) FROM agent_events WHERE task_id = ended.task_id), 0) + 1,
            gen_random_uuid(), ended.updated_at,
            jsonb_build_object('type', 'task.status', 'status', 'failed', 'error',
                jsonb_build_object('code', 'INTAKE_ROLLED_BACK',
                    'message', '需求问卷版本已回退，请重新创建调研', 'retryable', false))
        FROM ended
        """
    )
    op.execute(
        """UPDATE research_tasks SET idempotency_key = NULL
           WHERE create_operation = 'create_from_destination'"""
    )
    op.execute("DELETE FROM task_commands WHERE operation = 'answer_questionnaire'")
    op.drop_table("task_questionnaires")
    op.drop_table("task_intake")
    op.drop_constraint("ck_task_commands_operation", "task_commands", type_="check")
    op.alter_column("task_commands", "operation", type_=sa.String(8))
    op.create_check_constraint(
        "ck_task_commands_operation", "task_commands", "operation IN ('pause', 'resume', 'answer')"
    )
    op.drop_constraint("uq_research_tasks_owner_key", "research_tasks", type_="unique")
    op.create_unique_constraint(
        "uq_research_tasks_owner_key", "research_tasks", ["owner_id", "idempotency_key"]
    )
    op.drop_column("research_tasks", "create_operation")
