"""Persist verified identity profiles and backchannel logout state."""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.Text()))
    op.add_column("users", sa.Column("email_verified", sa.Boolean()))
    op.add_column("users", sa.Column("avatar_url", sa.Text()))
    op.add_column("sessions", sa.Column("oidc_client_id", sa.Text()))
    op.add_column("sessions", sa.Column("oidc_sid", sa.Text()))
    op.create_index("ix_sessions_oidc_sid", "sessions", ["oidc_client_id", "oidc_sid"])
    op.add_column("oidc_login_flows", sa.Column("expected_user_id", sa.Uuid()))
    op.add_column("oidc_login_flows", sa.Column("source_session_hash", sa.LargeBinary()))
    op.create_foreign_key(
        "fk_oidc_login_flows_expected_user_id",
        "oidc_login_flows",
        "users",
        ["expected_user_id"],
        ["user_id"],
        ondelete="CASCADE",
    )
    op.create_table(
        "oidc_logout_receipts",
        sa.Column("issuer", sa.Text(), primary_key=True),
        sa.Column("client_id", sa.Text(), primary_key=True),
        sa.Column("jti", sa.Text(), primary_key=True),
        sa.Column("sid", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_oidc_logout_receipts_sid", "oidc_logout_receipts", ["issuer", "client_id", "sid"]
    )
    op.create_index("ix_oidc_logout_receipts_expires_at", "oidc_logout_receipts", ["expires_at"])


def downgrade() -> None:
    op.drop_table("oidc_logout_receipts")
    op.drop_constraint(
        "fk_oidc_login_flows_expected_user_id", "oidc_login_flows", type_="foreignkey"
    )
    op.drop_column("oidc_login_flows", "source_session_hash")
    op.drop_column("oidc_login_flows", "expected_user_id")
    op.drop_index("ix_sessions_oidc_sid", table_name="sessions")
    op.drop_column("sessions", "oidc_sid")
    op.drop_column("sessions", "oidc_client_id")
    op.drop_column("users", "avatar_url")
    op.drop_column("users", "email_verified")
    op.drop_column("users", "email")
