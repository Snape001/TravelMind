from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TaskRow(Base):
    __tablename__ = "research_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'needs_clarification', 'paused', "
            "'completed', 'failed')",
            name="ck_research_tasks_status",
        ),
        UniqueConstraint("task_id", "trace_id", name="uq_research_tasks_task_trace"),
    )

    task_id: Mapped[UUID] = mapped_column(primary_key=True)
    trace_id: Mapped[UUID] = mapped_column(unique=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    brief: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24), server_default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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
    )

    task_id: Mapped[UUID] = mapped_column(primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trace_id: Mapped[UUID]
    event_id: Mapped[UUID] = mapped_column(unique=True)
    version: Mapped[int] = mapped_column(server_default="1")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB)


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
