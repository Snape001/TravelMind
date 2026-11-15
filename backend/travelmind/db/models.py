from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DEFAULT_MAX_ATTEMPTS = 3


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("issuer", "subject", name="uq_users_identity"),)

    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    issuer: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    email_verified: Mapped[bool | None]
    avatar_url: Mapped[str | None] = mapped_column(Text)


class SessionRow(Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_oidc_sid", "oidc_client_id", "oidc_sid"),)

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    oidc_client_id: Mapped[str | None] = mapped_column(Text)
    oidc_sid: Mapped[str | None] = mapped_column(Text)


class LoginFlowRow(Base):
    __tablename__ = "oidc_login_flows"

    state_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    browser_hash: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[str] = mapped_column(Text)
    verifier: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    expected_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE")
    )
    source_session_hash: Mapped[bytes | None] = mapped_column(LargeBinary)


class LogoutReceiptRow(Base):
    __tablename__ = "oidc_logout_receipts"
    __table_args__ = (Index("ix_oidc_logout_receipts_sid", "issuer", "client_id", "sid"),)

    issuer: Mapped[str] = mapped_column(Text, primary_key=True)
    client_id: Mapped[str] = mapped_column(Text, primary_key=True)
    jti: Mapped[str] = mapped_column(Text, primary_key=True)
    sid: Mapped[str] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class TaskRow(Base):
    __tablename__ = "research_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'needs_clarification', 'paused', "
            "'completed', 'failed')",
            name="ck_research_tasks_status",
        ),
        UniqueConstraint("task_id", "trace_id", name="uq_research_tasks_task_trace"),
        UniqueConstraint(
            "owner_id", "create_operation", "idempotency_key", name="uq_research_tasks_owner_key"
        ),
        CheckConstraint(
            "lease_epoch >= 0 AND attempt_count >= 0 AND projected_index >= 0",
            name="ck_research_tasks_worker_counters",
        ),
        CheckConstraint(
            "(lease_worker_id IS NULL) = (lease_until IS NULL)",
            name="ck_research_tasks_lease_pair",
        ),
        CheckConstraint(
            "(lease_worker_id IS NULL) = (lease_execution_permitted IS NULL)",
            name="ck_research_tasks_lease_permission",
        ),
        CheckConstraint(
            "max_attempts >= 1 AND attempt_count <= max_attempts",
            name="ck_research_tasks_attempt_budget",
        ),
        Index(
            "ix_research_tasks_claim",
            "available_at",
            "created_at",
            "task_id",
            postgresql_where=text("owner_id IS NOT NULL AND status = 'queued'"),
        ),
        Index(
            "ix_research_tasks_reclaim",
            "lease_until",
            "task_id",
            postgresql_where=text("owner_id IS NOT NULL AND status = 'running'"),
        ),
    )

    task_id: Mapped[UUID] = mapped_column(primary_key=True)
    trace_id: Mapped[UUID] = mapped_column(unique=True)
    owner_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.user_id"), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    create_operation: Mapped[str] = mapped_column(String(24), server_default="create_task")
    brief: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24), server_default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_worker_id: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_epoch: Mapped[int] = mapped_column(BigInteger, server_default="0")
    lease_execution_permitted: Mapped[bool | None]
    attempt_count: Mapped[int] = mapped_column(server_default="0")
    max_attempts: Mapped[int] = mapped_column(server_default=str(DEFAULT_MAX_ATTEMPTS))
    pause_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    instruction: Mapped[dict] = mapped_column(JSONB)
    paused_interrupt_id: Mapped[str | None] = mapped_column(Text)
    projected_index: Mapped[int] = mapped_column(BigInteger, server_default="0")


class ClarificationRow(Base):
    __tablename__ = "task_clarifications"
    __table_args__ = (
        Index(
            "uq_task_clarifications_pending",
            "task_id",
            unique=True,
            postgresql_where=text("answered_at IS NULL"),
        ),
    )

    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_tasks.task_id", ondelete="CASCADE"), primary_key=True
    )
    clarification_id: Mapped[UUID] = mapped_column(primary_key=True)
    questions: Mapped[list] = mapped_column(JSONB)
    answers: Mapped[list | None] = mapped_column(JSONB(none_as_null=True))
    interrupt_id: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CommandRow(Base):
    __tablename__ = "task_commands"
    __table_args__ = (
        CheckConstraint(
            "operation IN ('pause', 'resume', 'answer', 'answer_questionnaire')",
            name="ck_task_commands_operation",
        ),
    )

    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.user_id"), primary_key=True)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_tasks.task_id", ondelete="CASCADE"), primary_key=True
    )
    operation: Mapped[str] = mapped_column(String(24), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    digest: Mapped[bytes] = mapped_column(LargeBinary)
    status_code: Mapped[int]
    response: Mapped[dict] = mapped_column(JSONB)


class IntakeRow(Base):
    __tablename__ = "task_intake"

    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_tasks.task_id", ondelete="CASCADE"), primary_key=True
    )
    original_destination: Mapped[str] = mapped_column(String(80))
    search_text: Mapped[str] = mapped_column(String(80))
    destination: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    planner_context: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))


class QuestionnaireRow(Base):
    __tablename__ = "task_questionnaires"
    __table_args__ = (
        ForeignKeyConstraint(
            ["task_id", "questionnaire_id"],
            ["task_clarifications.task_id", "task_clarifications.clarification_id"],
            ondelete="CASCADE",
        ),
    )

    task_id: Mapped[UUID] = mapped_column(primary_key=True)
    questionnaire_id: Mapped[UUID] = mapped_column(primary_key=True)
    questionnaire: Mapped[dict] = mapped_column(JSONB)


class ProjectionRow(Base):
    __tablename__ = "task_projections"

    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_tasks.task_id", ondelete="CASCADE"), primary_key=True
    )
    projection_index: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(Text)
    digest: Mapped[bytes] = mapped_column(LargeBinary)


class AttemptFailureRow(Base):
    __tablename__ = "task_attempt_failures"

    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_tasks.task_id", ondelete="CASCADE"), primary_key=True
    )
    failure_id: Mapped[UUID] = mapped_column(primary_key=True)
    digest: Mapped[bytes] = mapped_column(LargeBinary)


class EventRow(Base):
    __tablename__ = "agent_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["task_id", "trace_id"],
            ["research_tasks.task_id", "research_tasks.trace_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("sequence BETWEEN 1 AND 9007199254740991", name="ck_agent_events_seq"),
        CheckConstraint("version = 1", name="ck_agent_events_protocol"),
        UniqueConstraint("task_id", "projection_key", name="uq_agent_events_projection"),
    )

    task_id: Mapped[UUID] = mapped_column(primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trace_id: Mapped[UUID]
    event_id: Mapped[UUID] = mapped_column(unique=True)
    version: Mapped[int] = mapped_column(server_default="1")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB)
    projection_key: Mapped[UUID | None]


class AgentRunRow(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')", name="ck_agent_runs_status"
        ),
    )

    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_tasks.task_id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[str] = mapped_column(String(64))
    assignment_id: Mapped[UUID | None]
    status: Mapped[str] = mapped_column(String(16))
    result: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvidenceRow(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            "source_url IS NOT NULL OR source_locator IS NOT NULL", name="ck_evidence_reference"
        ),
        CheckConstraint(
            "source_type IN ('web', 'official', 'academic', 'book', 'social', 'map')",
            name="ck_evidence_source_type",
        ),
    )

    evidence_id: Mapped[UUID] = mapped_column(primary_key=True)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_tasks.task_id", ondelete="CASCADE"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(16))
    source_title: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_locator: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    excerpt: Mapped[str] = mapped_column(Text)


class ReportRow(Base):
    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_reports_version"),
        UniqueConstraint("task_id", "version", name="uq_reports_task_version"),
    )

    report_id: Mapped[UUID] = mapped_column(primary_key=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("research_tasks.task_id", ondelete="CASCADE"))
    version: Mapped[int]
    content: Mapped[dict] = mapped_column(JSONB)
    map_data: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskMapRow(Base):
    __tablename__ = "task_map_data"

    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_tasks.task_id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[dict] = mapped_column(JSONB)


class ResearchSourceRow(Base):
    __tablename__ = "research_sources"
    __table_args__ = (
        UniqueConstraint("task_id", "evidence_id", name="uq_research_source_evidence"),
    )

    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_tasks.task_id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[UUID] = mapped_column(primary_key=True)
    evidence_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("evidence.evidence_id", ondelete="SET NULL")
    )
    data: Mapped[dict] = mapped_column(JSONB)
    fetch_attempt: Mapped[int] = mapped_column(server_default="0")


class ResearchEventRow(Base):
    __tablename__ = "research_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["task_id", "trace_id"],
            ["research_tasks.task_id", "research_tasks.trace_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("sequence BETWEEN 1 AND 9007199254740991", name="ck_research_events_seq"),
    )

    task_id: Mapped[UUID] = mapped_column(primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trace_id: Mapped[UUID]
    event_id: Mapped[UUID] = mapped_column(unique=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB)
