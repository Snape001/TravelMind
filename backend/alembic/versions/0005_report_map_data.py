"""Persist task and report map snapshots."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_map_data",
        sa.Column("task_id", sa.Uuid(), primary_key=True),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["research_tasks.task_id"], ondelete="CASCADE"),
    )
    op.add_column("reports", sa.Column("map_data", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("reports", "map_data")
    op.drop_table("task_map_data")
