from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID, uuid4, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from travelmind.db.models import ResearchSourceRow
from travelmind.db.research_browser import (
    append_event,
    ensure_source,
    source_for_evidence,
    source_id_for,
)
from travelmind.db.worker import PostgresWorkerStore
from travelmind.schemas.agents import AgentInput
from travelmind.schemas.domain import Evidence
from travelmind.schemas.phase4_events import (
    AgentUsage,
    AgentUsageEvent,
    ResearchBrowserPayload,
    SourceFetchEvent,
    SourceScoredEvent,
)
from travelmind.schemas.research_quality import EvidenceQuality
from travelmind.schemas.worker import LeaseLost, LeaseRef


@dataclass(frozen=True)
class ResearchTelemetry:
    store: PostgresWorkerStore
    lease: LeaseRef
    trace_id: UUID

    def validate_request(self, request: AgentInput) -> None:
        if request.task_id != self.lease.task_id or request.trace_id != self.trace_id:
            raise LeaseLost("research event does not match the lease")

    async def _append(
        self,
        session: AsyncSession,
        event_id: UUID,
        payload: ResearchBrowserPayload,
    ) -> None:
        await append_event(session, self.lease.task_id, self.trace_id, event_id, payload)

    async def discover(self, request: AgentInput, evidence: list[Evidence]) -> dict[UUID, UUID]:
        self.validate_request(request)
        if not evidence:
            return {}
        async with self.store.sessions.begin() as session:
            await self.store.check_lease(self.lease, transaction=session)
            sources = {}
            for item in evidence:
                source = await ensure_source(
                    session,
                    request.task_id,
                    request.trace_id,
                    item,
                    agent_id=request.agent_id,
                    run_id=request.run_id,
                )
                sources[source_id_for(request.task_id, item)] = source.source_id
            return sources

    async def start_fetch(self, request: AgentInput, source_id: UUID) -> int:
        self.validate_request(request)
        async with self.store.sessions.begin() as session:
            await self.store.check_lease(self.lease, transaction=session)
            row = await session.get(ResearchSourceRow, (request.task_id, source_id))
            row.fetch_attempt += 1
            await self._append(
                session,
                uuid5(source_id, f"fetch:{row.fetch_attempt}:started"),
                SourceFetchEvent(
                    type="research.source.fetch",
                    source_id=source_id,
                    status="started",
                    attempt=row.fetch_attempt,
                    agent_id=request.agent_id,
                    run_id=request.run_id,
                ),
            )
            return row.fetch_attempt

    async def finish_fetch(
        self,
        request: AgentInput,
        source_id: UUID,
        attempt: int,
        *,
        body: str | None,
        error_code: str | None,
    ) -> None:
        self.validate_request(request)
        async with self.store.sessions.begin() as session:
            await self.store.check_lease(self.lease, transaction=session)
            row = await session.get(ResearchSourceRow, (request.task_id, source_id))
            if row.fetch_attempt != attempt:
                return
            if body:
                row.data = {**row.data, "body_text": body[:50000].strip() or None}
            await self._append(
                session,
                uuid5(source_id, f"fetch:{attempt}:finished"),
                SourceFetchEvent(
                    type="research.source.fetch",
                    source_id=source_id,
                    attempt=attempt,
                    status="failed" if error_code else "succeeded",
                    error_code=error_code,
                    agent_id=request.agent_id,
                    run_id=request.run_id,
                ),
            )

    async def link_evidence(
        self,
        session: AsyncSession,
        request: AgentInput,
        items: list[Evidence],
        aliases: dict[UUID, UUID],
    ) -> None:
        self.validate_request(request)
        for item in items:
            if await source_for_evidence(session, request.task_id, item.evidence_id) is not None:
                continue
            source_id = source_id_for(request.task_id, item)
            source = await session.get(
                ResearchSourceRow, (request.task_id, aliases.get(source_id, source_id))
            )
            if source is None:
                source = await ensure_source(
                    session,
                    request.task_id,
                    self.trace_id,
                    item,
                    agent_id=request.agent_id,
                    run_id=request.run_id,
                )
            source.evidence_id = item.evidence_id

    async def scored(self, request: AgentInput, cards: list[EvidenceQuality]) -> None:
        self.validate_request(request)
        if not cards:
            return
        async with self.store.sessions.begin() as session:
            await self.store.check_lease(self.lease, transaction=session)
            for card in cards:
                source = await source_for_evidence(session, request.task_id, card.evidence_id)
                if source is None:
                    continue
                await self._append(
                    session,
                    uuid5(request.run_id, f"scored:{card.assignment_id}:{card.evidence_id}"),
                    SourceScoredEvent(
                        type="research.source.scored",
                        source_id=source.source_id,
                        evidence_id=card.evidence_id,
                        assignment_id=card.assignment_id,
                        policy_version=card.policy_version,
                        level=card.level,
                        total_score=card.total_score,
                        agent_id=request.agent_id,
                        run_id=request.run_id,
                    ),
                )

    async def usage(
        self,
        request: AgentInput,
        *,
        model: str,
        prompt_preview: str | None,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None:
        self.validate_request(request)
        call_id = uuid4()
        async with self.store.sessions.begin() as session:
            await self.store.check_lease(self.lease, transaction=session)
            await self._append(
                session,
                call_id,
                AgentUsageEvent(
                    type="agent.usage",
                    agent_id=request.agent_id,
                    run_id=request.run_id,
                    usage=AgentUsage(
                        call_id=call_id,
                        provider="deepseek",
                        model=model[:200],
                        prompt_preview=prompt_preview,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    ),
                ),
            )


current_telemetry: ContextVar[ResearchTelemetry | None] = ContextVar(
    "current_research_telemetry", default=None
)
