"""Add durable task control and identity tables."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    time = sa.DateTime(timezone=True)
    op.create_table(
        "users",
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text()),
        sa.UniqueConstraint("issuer", "subject", name="uq_users_identity"),
    )
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.LargeBinary(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("expires_at", time, nullable=False),
        sa.Column("revoked_at", time),
    )
    op.create_table(
        "oidc_login_flows",
        sa.Column("state_hash", sa.LargeBinary(), primary_key=True),
        sa.Column("browser_hash", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("verifier", sa.Text(), nullable=False),
        sa.Column("expires_at", time, nullable=False),
    )
    op.create_index("ix_oidc_login_flows_expires_at", "oidc_login_flows", ["expires_at"])
    task_table = "research_tasks"
    for column in (
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.user_id")),
        sa.Column("available_at", time),
        sa.Column("lease_worker_id", sa.Uuid()),
        sa.Column("lease_until", time),
        sa.Column("lease_epoch", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("pause_requested_at", time),
        sa.Column("instruction", JSONB()),
        sa.Column("paused_interrupt_id", sa.Text()),
        sa.Column("projected_index", sa.BigInteger(), server_default="0", nullable=False),
    ):
        op.add_column(task_table, column)

    # Phase 1 has no checkpoints. Keep the start instruction for any future ownership mapping.
    op.execute(
        """UPDATE research_tasks
        SET instruction = jsonb_build_object(
            'kind', 'start', 'instruction_id', gen_random_uuid(), 'brief', brief
        )"""
    )
    op.alter_column(task_table, "instruction", nullable=False)
    op.execute(
        """WITH invalidated AS (
            UPDATE research_tasks SET status = 'failed', updated_at = now()
            WHERE status IN ('running', 'needs_clarification', 'paused')
            RETURNING task_id, trace_id, updated_at
        )
        INSERT INTO agent_events (task_id, trace_id, sequence, event_id, occurred_at, payload)
        SELECT t.task_id, t.trace_id,
            COALESCE((SELECT MAX(e.sequence) FROM agent_events e WHERE e.task_id = t.task_id), 0)+1,
            gen_random_uuid(), t.updated_at,
            jsonb_build_object('type', 'task.status', 'status', 'failed', 'error',
                jsonb_build_object('code', 'LEGACY_TASK_INTERRUPTED',
                    'message', '旧执行器任务缺少可恢复的检查点', 'retryable', false))
        FROM invalidated t"""
    )
    op.drop_constraint("research_tasks_idempotency_key_key", task_table, type_="unique")
    op.create_unique_constraint(
        "uq_research_tasks_owner_key", task_table, ["owner_id", "idempotency_key"]
    )
    op.create_check_constraint(
        "ck_research_tasks_worker_counters",
        task_table,
        "lease_epoch >= 0 AND attempt_count >= 0 AND projected_index >= 0",
    )
    op.create_check_constraint(
        "ck_research_tasks_lease_pair",
        task_table,
        "(lease_worker_id IS NULL) = (lease_until IS NULL)",
    )
    op.create_index("ix_research_tasks_owner_id", task_table, ["owner_id"])
    op.create_index(
        "ix_research_tasks_claim",
        task_table,
        ["available_at", "created_at", "task_id"],
        postgresql_where=sa.text("owner_id IS NOT NULL AND status = 'queued'"),
    )
    op.create_index(
        "ix_research_tasks_reclaim",
        task_table,
        ["lease_until", "task_id"],
        postgresql_where=sa.text("owner_id IS NOT NULL AND status = 'running'"),
    )
    op.add_column("agent_events", sa.Column("projection_key", sa.Uuid()))
    op.create_unique_constraint(
        "uq_agent_events_projection", "agent_events", ["task_id", "projection_key"]
    )
    op.create_table(
        "task_clarifications",
        task_pk(),
        sa.Column("clarification_id", sa.Uuid(), primary_key=True),
        sa.Column("questions", JSONB(), nullable=False),
        sa.Column("answers", JSONB()),
        sa.Column("interrupt_id", sa.Text(), nullable=False),
        sa.Column("created_at", time, nullable=False),
        sa.Column("answered_at", time),
    )
    op.create_index(
        "uq_task_clarifications_pending",
        "task_clarifications",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text("answered_at IS NULL"),
    )
    op.create_table(
        "task_commands",
        task_pk(),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.user_id"), primary_key=True),
        sa.Column("operation", sa.String(8), primary_key=True),
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("digest", sa.LargeBinary(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response", JSONB(), nullable=False),
        sa.CheckConstraint(
            "operation IN ('pause', 'resume', 'answer')", name="ck_task_commands_operation"
        ),
    )
    op.create_table(
        "task_projections",
        task_pk(),
        sa.Column("projection_index", sa.BigInteger(), primary_key=True),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("digest", sa.LargeBinary(), nullable=False),
    )
    op.create_table(
        "task_attempt_failures",
        task_pk(),
        sa.Column("failure_id", sa.Uuid(), primary_key=True),
        sa.Column("digest", sa.LargeBinary(), nullable=False),
    )


def task_pk() -> sa.Column:
    return sa.Column(
        "task_id",
        sa.Uuid(),
        sa.ForeignKey("research_tasks.task_id", ondelete="CASCADE"),
        primary_key=True,
    )


def downgrade() -> None:
    op.drop_table("task_attempt_failures")
    op.drop_table("task_projections")
    op.drop_table("task_commands")
    op.drop_table("task_clarifications")
    op.drop_constraint("uq_agent_events_projection", "agent_events", type_="unique")
    op.drop_column("agent_events", "projection_key")
    task_table = "research_tasks"
    for index in (
        "ix_research_tasks_owner_id",
        "ix_research_tasks_claim",
        "ix_research_tasks_reclaim",
    ):
        op.drop_index(index, table_name=task_table)
    for constraint in ("ck_research_tasks_worker_counters", "ck_research_tasks_lease_pair"):
        op.drop_constraint(constraint, task_table, type_="check")
    op.drop_constraint("uq_research_tasks_owner_key", task_table, type_="unique")
    # Phase 1 has a global key namespace and cannot preserve cross-owner duplicate keys.
    op.execute(
        """UPDATE research_tasks SET idempotency_key = NULL WHERE task_id IN (
            SELECT task_id FROM (
                SELECT task_id, row_number() OVER (
                    PARTITION BY idempotency_key ORDER BY created_at, task_id
                ) AS rank FROM research_tasks WHERE idempotency_key IS NOT NULL
            ) keys WHERE rank > 1
        )"""
    )
    op.create_unique_constraint(
        "research_tasks_idempotency_key_key", task_table, ["idempotency_key"]
    )
    for column in (
        "owner_id",
        "available_at",
        "lease_worker_id",
        "lease_until",
        "lease_epoch",
        "attempt_count",
        "pause_requested_at",
        "instruction",
        "paused_interrupt_id",
        "projected_index",
    ):
        op.drop_column(task_table, column)
    op.drop_table("oidc_login_flows")
    op.drop_table("sessions")
    op.drop_table("users")
