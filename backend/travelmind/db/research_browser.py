from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import UUID, uuid5

from pydantic import HttpUrl
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from travelmind.db.models import EvidenceRow, ReportRow, ResearchEventRow, ResearchSourceRow
from travelmind.research.sources import is_public_target, source_key
from travelmind.schemas.domain import Evidence, ReportDraft
from travelmind.schemas.events import ReportReadyEvent
from travelmind.schemas.phase4_events import (
    ResearchBrowserEvent,
    ResearchBrowserPayload,
    SourceCitedEvent,
    SourceDiscoveredEvent,
)
from travelmind.schemas.phase4_sources import ResearchSourceRead

PRIVATE_PARAMETERS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "code",
    "key",
    "password",
    "secret",
    "session",
    "sessionid",
    "sig",
    "signature",
    "token",
    "xsec_token",
}


def public_link(url: HttpUrl | None) -> HttpUrl | None:
    if url is None or not is_public_target(url):
        return None
    parts = urlsplit(str(url))
    safe_query = [
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if name.casefold() not in PRIVATE_PARAMETERS and not name.casefold().startswith("x-amz-")
    ]
    return HttpUrl(parts._replace(query=urlencode(safe_query), fragment="").geturl())


def source_id_for(task_id: UUID, evidence: Evidence) -> UUID:
    return uuid5(task_id, "source:" + "\0".join(source_key(evidence)))


async def append_event(
    session: AsyncSession,
    task_id: UUID,
    trace_id: UUID,
    event_id: UUID,
    payload: ResearchBrowserPayload,
) -> None:
    # Every writer holds the task row lock before allocating a cursor.
    if await session.scalar(
        select(ResearchEventRow.event_id).where(ResearchEventRow.event_id == event_id)
    ):
        return
    sequence = await session.scalar(
        select(func.max(ResearchEventRow.sequence)).where(ResearchEventRow.task_id == task_id)
    )
    session.add(
        ResearchEventRow(
            task_id=task_id,
            trace_id=trace_id,
            event_id=event_id,
            sequence=(sequence or 0) + 1,
            occurred_at=datetime.now(UTC),
            payload=payload.model_dump(mode="json"),
        )
    )
    await session.flush()


async def ensure_source(
    session: AsyncSession,
    task_id: UUID,
    trace_id: UUID,
    item: Evidence,
    *,
    agent_id: str,
    run_id: UUID,
) -> ResearchSourceRow:
    source_id = source_id_for(task_id, item)
    row = await session.get(ResearchSourceRow, (task_id, source_id))
    if row is not None:
        return row
    display = ResearchSourceRead(
        task_id=task_id,
        source_id=source_id,
        source_type=item.source_type,
        title=item.source_title[:200],
        source_url=public_link(item.source_url),
    )
    row = ResearchSourceRow(
        task_id=task_id, source_id=source_id, data=display.model_dump(mode="json")
    )
    session.add(row)
    await append_event(
        session,
        task_id,
        trace_id,
        uuid5(source_id, "discovered"),
        SourceDiscoveredEvent(
            type="research.source.discovered",
            agent_id=agent_id,
            run_id=run_id,
            **display.model_dump(exclude={"task_id", "body_text"}),
        ),
    )
    return row


async def source_for_evidence(
    session: AsyncSession,
    task_id: UUID,
    evidence_id: UUID,
) -> ResearchSourceRow | None:
    return await session.scalar(
        select(ResearchSourceRow).where(
            ResearchSourceRow.task_id == task_id,
            ResearchSourceRow.evidence_id == evidence_id,
        )
    )


async def publish_citations(
    session: AsyncSession,
    report: ReportRow,
    trace_id: UUID,
    ready: ReportReadyEvent,
) -> None:
    draft = ReportDraft.model_validate(report.content)
    for evidence_id in dict.fromkeys(
        ref for section in draft.sections for ref in section.evidence_ids
    ):
        row = await session.get(EvidenceRow, evidence_id)
        if row is None or row.task_id != report.task_id:
            continue
        source = await source_for_evidence(session, report.task_id, evidence_id)
        if source is None:
            source = await ensure_source(
                session,
                report.task_id,
                trace_id,
                Evidence.model_validate(row, from_attributes=True),
                agent_id=ready.agent_id,
                run_id=ready.run_id,
            )
            source.evidence_id = evidence_id
        await append_event(
            session,
            report.task_id,
            trace_id,
            uuid5(report.report_id, f"cited:{evidence_id}"),
            SourceCitedEvent(
                type="research.source.cited",
                agent_id=ready.agent_id,
                run_id=ready.run_id,
                source_id=source.source_id,
                evidence_id=evidence_id,
                report_id=report.report_id,
                report_version=report.version,
            ),
        )


async def research_events_after(
    session: AsyncSession,
    task_id: UUID,
    cursor: int,
) -> list[ResearchBrowserEvent]:
    rows = await session.scalars(
        select(ResearchEventRow)
        .where(ResearchEventRow.task_id == task_id, ResearchEventRow.sequence > cursor)
        .order_by(ResearchEventRow.sequence)
        .limit(100)
    )
    return [ResearchBrowserEvent.model_validate(item, from_attributes=True) for item in rows]
