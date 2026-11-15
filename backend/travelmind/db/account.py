from datetime import datetime
from uuid import UUID

from sqlalchemy import Text, cast, select, true, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from travelmind.db.models import EventRow, ReportRow, TaskRow
from travelmind.schemas.api import ReportSummary


async def published_reports(
    session: AsyncSession,
    owner_id: UUID,
    *,
    limit: int,
    before: tuple[datetime, UUID] | None,
) -> list[ReportSummary]:
    latest = (
        select(
            EventRow.payload["report_id"].astext.label("report_id"),
            EventRow.occurred_at.label("published_at"),
        )
        .where(
            EventRow.task_id == TaskRow.task_id, EventRow.payload["type"].astext == "report.ready"
        )
        .correlate(TaskRow)
        .order_by(EventRow.sequence.desc())
        .limit(1)
        .lateral()
    )
    statement = (
        select(
            TaskRow.task_id,
            ReportRow.report_id,
            ReportRow.version,
            ReportRow.content["title"].astext.label("title"),
            latest.c.published_at,
        )
        .select_from(TaskRow)
        .join(latest, true())
        .join(
            ReportRow,
            (ReportRow.task_id == TaskRow.task_id)
            & (cast(ReportRow.report_id, Text) == latest.c.report_id),
        )
        .where(TaskRow.owner_id == owner_id)
        .order_by(latest.c.published_at.desc(), ReportRow.report_id.desc())
        .limit(limit)
    )
    if before is not None:
        statement = statement.where(tuple_(latest.c.published_at, ReportRow.report_id) < before)
    results = await session.execute(statement)
    return [ReportSummary.model_validate(row) for row in results.mappings()]
