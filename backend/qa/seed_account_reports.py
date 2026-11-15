"""Seed synthetic reports for account QA in a dedicated, migrated test database."""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from asyncpg import PostgresError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from travelmind.config import Settings
from travelmind.db.connection import create_database_engine
from travelmind.db.evidence import save_evidence
from travelmind.db.models import ReportRow, TaskRow, UserRow
from travelmind.db.research_browser import ensure_source
from travelmind.db.tasks import store_event
from travelmind.schemas.domain import (
    Evidence,
    ReportDraft,
    ReportSection,
    ResearchBrief,
    TaskStatus,
)
from travelmind.schemas.events import ReportReadyEvent, TaskStatusEvent
from travelmind.schemas.worker import StartResearch

BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


class FixtureError(ValueError):
    pass


async def _task(
    session: AsyncSession, owner_id: UUID, title: str, status: TaskStatus = "completed"
) -> TaskRow:
    brief = ResearchBrief(destination=title)
    task = TaskRow(
        task_id=uuid4(),
        trace_id=uuid4(),
        owner_id=owner_id,
        status=status,
        brief=brief.model_dump(mode="json"),
        instruction=StartResearch(kind="start", instruction_id=uuid4(), brief=brief).model_dump(
            mode="json"
        ),
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
        available_at=None,
    )
    session.add(task)
    await session.flush()
    store_event(session, task, 1, TaskStatusEvent(type="task.status", status="queued"), BASE_TIME)
    return task


def _report(
    session: AsyncSession,
    task: TaskRow,
    title: str,
    version: int = 1,
    *,
    report_id: UUID | None = None,
    publish_day: int | None = None,
    created_at: datetime = BASE_TIME,
) -> dict:
    report_id = report_id or uuid4()
    content = ReportDraft(
        title=title,
        sections=[
            ReportSection(
                heading="QA fixture", body="Synthetic report.", author_agent_ids=["writer"]
            )
        ],
    )
    published_at = None if publish_day is None else BASE_TIME + timedelta(days=publish_day)
    session.add(
        ReportRow(
            report_id=report_id,
            task_id=task.task_id,
            version=version,
            content=content.model_dump(mode="json"),
            created_at=created_at,
        )
    )
    if published_at is not None:
        store_event(
            session,
            task,
            version + 1,
            ReportReadyEvent(
                type="report.ready",
                agent_id="writer",
                run_id=uuid4(),
                report_id=report_id,
                version=version,
            ),
            published_at,
        )
    return {
        "task_id": str(task.task_id),
        "report_id": str(report_id),
        "version": version,
        "title": title,
        "published_at": published_at.isoformat() if published_at is not None else None,
    }


async def seed_reports(session: AsyncSession, alice_id: UUID, bob_id: UUID) -> dict:
    """Return a report-index oracle and resource IDs. The caller owns the transaction."""
    if alice_id == bob_id:
        raise FixtureError("Alice and Bob must have different TravelMind user IDs.")
    owners = await session.scalars(
        select(UserRow.user_id).where(UserRow.user_id.in_((alice_id, bob_id)))
    )
    if set(owners) != {alice_id, bob_id}:
        raise FixtureError("Sign in as both QA users first and copy their TravelMind user IDs.")

    revised = await _task(session, alice_id, "QA Alice revised")
    previous = _report(session, revised, "QA Alice previous revision", publish_day=1)
    latest = _report(
        session,
        revised,
        "QA Alice latest revision",
        2,
        publish_day=3,
        created_at=BASE_TIME + timedelta(days=2),
    )
    draft = _report(session, revised, "QA Alice draft", 3, created_at=BASE_TIME + timedelta(days=5))

    low_id, high_id = sorted((uuid4(), uuid4()))
    low_task = await _task(session, alice_id, "QA Alice tie low")
    high_task = await _task(session, alice_id, "QA Alice tie high")
    oldest_task = await _task(session, alice_id, "QA Alice oldest")
    legacy_task = await _task(session, alice_id, "QA Alice legacy")
    bob_task = await _task(session, bob_id, "QA Bob only")
    idle = await _task(session, alice_id, "QA Alice idle", "queued")

    [evidence] = await save_evidence(
        session,
        idle.task_id,
        [
            Evidence(
                evidence_id=uuid4(),
                source_type="book",
                source_title="QA synthetic source",
                source_locator="QA fixture, page 1",
                retrieved_at=BASE_TIME,
                excerpt="Synthetic evidence for ownership and SSE checks.",
            )
        ],
    )
    source = await ensure_source(
        session, idle.task_id, idle.trace_id, evidence, agent_id="research_director", run_id=uuid4()
    )
    return {
        "alice_index": [
            latest,
            _report(session, high_task, "QA Alice tie high", report_id=high_id, publish_day=2),
            _report(session, low_task, "QA Alice tie low", report_id=low_id, publish_day=2),
            _report(session, oldest_task, "QA Alice oldest", publish_day=1),
        ],
        "bob_index": [_report(session, bob_task, "QA Bob only", publish_day=4)],
        "alice_previous": previous,
        "alice_draft": draft,
        "alice_legacy": _report(session, legacy_task, "QA Alice legacy"),
        "alice_idle": {
            "task_id": str(idle.task_id),
            "source_id": str(source.source_id),
            "evidence_id": str(evidence.evidence_id),
        },
    }


async def _seed_database(database_url: str, alice_id: UUID, bob_id: UUID) -> dict:
    settings = Settings(_env_file=None, database_url=database_url)
    engine = create_database_engine(settings)
    try:
        sessions = async_sessionmaker(engine)
        async with sessions.begin() as session:
            return await seed_reports(session, alice_id, bob_id)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alice-user-id", type=UUID, required=True)
    parser.add_argument("--bob-user-id", type=UUID, required=True)
    args = parser.parse_args()
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        parser.error("Set TEST_DATABASE_URL to the dedicated browser QA database.")
    try:
        manifest = asyncio.run(_seed_database(database_url, args.alice_user_id, args.bob_user_id))
    except FixtureError as error:
        parser.exit(1, f"{error}\n")
    except (SQLAlchemyError, PostgresError, OSError, ValidationError):
        parser.exit(1, "Could not seed the QA database. Check migration and connectivity.\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
