import asyncio
import logging
from collections import defaultdict

from pydantic import HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from travelmind.db.evidence import save_evidence
from travelmind.research.engine import ResearchEngine, ToolResult
from travelmind.research.sources import is_public_target, url_key
from travelmind.runtime.execution import RunError
from travelmind.schemas.agents import AgentInput
from travelmind.schemas.domain import Evidence
from travelmind.schemas.worker import LeaseLost, LeaseRef, WorkerStore

logger = logging.getLogger(__name__)


class ResearchRetriever:
    def __init__(
        self,
        engine: ResearchEngine,
        sessions: async_sessionmaker[AsyncSession],
        *,
        store: WorkerStore[AsyncSession],
        lease: LeaseRef,
    ):
        self.engine = engine
        self.sessions = sessions
        self.store = store
        self.lease = lease
        self._scrape_locks = defaultdict(asyncio.Lock)
        self._scrape_results: dict[tuple[str, str], ToolResult] = {}

    async def _scrape(self, url: HttpUrl, request: AgentInput) -> ToolResult:
        key = url_key(url)
        try:
            # Keep the scrape and any wait for a parallel expert inside the same budget.
            async with asyncio.timeout(min(12, request.budget.timeout_seconds / 2)):
                async with self._scrape_locks[key]:
                    if key not in self._scrape_results:
                        self._scrape_results[key] = await self.engine.scrape(url)
                    return self._scrape_results[key]
        except TimeoutError:
            logger.warning(
                "research.scrape_timeout task_id=%s trace_id=%s",
                request.task_id,
                request.trace_id,
            )
            return ToolResult()

    async def __call__(self, request: AgentInput) -> list[Evidence]:
        if request.task_id != self.lease.task_id:
            raise LeaseLost("evidence task does not match the lease")
        brief = request.brief
        objective = request.assignment.objective if request.assignment else ""
        query = f"{brief.destination} {' '.join(brief.topics[:3])} {objective}".strip()[:1000]
        sources = ("bocha", "openalex") if brief.depth == "academic" else ("bocha",)
        search = await self.engine.search(query, sources=sources, limit=3)
        if not search.evidence and "openalex" not in sources:
            fallback = await self.engine.search(query, sources=("openalex",), limit=3)
            search.evidence.extend(fallback.evidence)
            search.errors.extend(fallback.errors)

        evidence = search.evidence
        if not evidence and search.errors:
            problem = search.errors[-1]
            raise RunError(problem.code, problem.message, retryable=problem.retryable)
        if brief.depth != "quick":
            for index, item in enumerate(evidence):
                url = item.source_url
                if item.source_type in ("web", "official") and url and is_public_target(url):
                    page = await self._scrape(url, request)
                    if page.evidence:
                        evidence = [*page.evidence, *evidence[:index], *evidence[index + 1 :]]
                    search.errors.extend(page.errors)
                    break

        for error in search.errors:
            logger.warning(
                "research.partial task_id=%s trace_id=%s provider=%s code=%s",
                request.task_id,
                request.trace_id,
                error.provider,
                error.code,
            )
        async with self.sessions.begin() as session:
            await self.store.check_lease(self.lease, transaction=session)
            return await save_evidence(session, request.task_id, evidence)
