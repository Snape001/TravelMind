from collections.abc import Iterable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from travelmind.db.models import EvidenceRow, TaskRow
from travelmind.research.sources import source_key, unique_sources
from travelmind.schemas.domain import Evidence


async def save_evidence(
    session: AsyncSession, task_id: UUID, items: Iterable[Evidence]
) -> list[Evidence]:
    """Store the first evidence for each source and return the IDs to use in citations.

    The caller owns the transaction. Writers using this function serialize on the task row,
    which also lets them reuse evidence stored before source deduplication was introduced.
    """
    candidates = unique_sources(items)
    if not candidates:
        return []

    task = await session.scalar(
        select(TaskRow.task_id).where(TaskRow.task_id == task_id).with_for_update()
    )
    if task is None:
        raise LookupError("research task does not exist")

    rows = await session.scalars(
        select(EvidenceRow)
        .where(EvidenceRow.task_id == task_id)
        .order_by(EvidenceRow.retrieved_at, EvidenceRow.evidence_id)
    )
    existing = {}
    for row in rows:
        item = Evidence.model_validate(row, from_attributes=True)
        existing.setdefault(source_key(item), item)
    stored = []
    for item in candidates:
        prior = existing.get(source_key(item))
        if prior is not None:
            stored.append(prior)
            continue
        stored.append(await _insert_evidence(session, task_id, item))
    return stored


async def _insert_evidence(session: AsyncSession, task_id: UUID, item: Evidence) -> Evidence:
    values = item.model_dump()
    values["source_url"] = str(item.source_url) if item.source_url is not None else None
    command = (
        insert(EvidenceRow)
        .values(task_id=task_id, **values)
        .on_conflict_do_nothing(index_elements=[EvidenceRow.evidence_id])
        .returning(EvidenceRow)
    )
    while (row := await session.scalar(command)) is None:
        # Evidence IDs are global, including when callers reuse evidence across tasks.
        command = command.values(evidence_id=uuid4())
    return Evidence.model_validate(row, from_attributes=True)
