import asyncio
import logging
import re
from collections import defaultdict

from pydantic import HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from travelmind.db.evidence import save_evidence
from travelmind.research.engine import ResearchEngine, SearchProvider, ToolResult
from travelmind.research.http import failure
from travelmind.research.sources import is_public_target, url_key
from travelmind.runtime.execution import RunError
from travelmind.schemas.agents import AgentInput
from travelmind.schemas.domain import Evidence
from travelmind.schemas.worker import LeaseLost, LeaseRef, WorkerStore

logger = logging.getLogger(__name__)
HISTORY_TOPICS = {"历史文化", "历史与地方文化", "地方文化", "地方史", "文化遗产", "民俗"}


def research_query(request: AgentInput) -> str:
    brief = request.brief
    objective = request.assignment.objective if request.assignment else ""
    return " ".join((brief.destination, *brief.topics[:3], objective)).strip()[:1000]


def library_query(request: AgentInput) -> str:
    brief = request.brief
    destination = re.sub(r"[?？*＊]+", "", brief.destination).strip()
    for topic in brief.topics:
        term = re.sub(r"[?？*＊]+", "", topic).strip(" 《》")
        if 2 <= len(term) <= 24 and term not in HISTORY_TOPICS and term != destination:
            return term if destination in term else f"{destination} {term}".strip()
    return destination


class ResearchRetriever:
    def __init__(
        self,
        engine: ResearchEngine,
        sessions: async_sessionmaker[AsyncSession],
        *,
        store: WorkerStore[AsyncSession],
        lease: LeaseRef,
        library_enabled: bool = False,
    ):
        self.engine = engine
        self.sessions = sessions
        self.store = store
        self.lease = lease
        self.library_enabled = library_enabled
        self._library_lock = asyncio.Lock()
        self._library_result: ToolResult | None = None
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

    async def _web_search(self, request: AgentInput, query: str) -> ToolResult:
        sources: tuple[SearchProvider, ...] = (
            ("bocha", "openalex") if request.brief.depth == "academic" else ("bocha",)
        )
        search = await self.engine.search(query, sources=sources, limit=3)
        if not search.evidence and "openalex" not in sources:
            fallback = await self.engine.search(query, sources=("openalex",), limit=3)
            search.evidence.extend(fallback.evidence)
            search.errors.extend(fallback.errors)
        return search

    async def _search_library(self, request: AgentInput) -> ToolResult:
        async with self._library_lock:
            if self._library_result is None:
                query = library_query(request)
                try:
                    async with asyncio.timeout(min(8, request.budget.timeout_seconds / 3)):
                        self._library_result = (
                            await self.engine.search(query, sources=("cnkgraph",), limit=2)
                            if len(query) >= 2
                            else ToolResult()
                        )
                except TimeoutError:
                    error = failure("cnkgraph", "TIMEOUT", "古籍检索超时", retryable=True)
                    self._library_result = ToolResult(errors=[error.error])
            return self._library_result

    async def __call__(self, request: AgentInput) -> list[Evidence]:
        if request.task_id != self.lease.task_id:
            raise LeaseLost("evidence task does not match the lease")
        brief = request.brief
        query = research_query(request)
        if (
            self.library_enabled
            and request.agent_id == "history_culture"
            and brief.depth != "quick"
        ):
            search, library = await asyncio.gather(
                self._web_search(request, query), self._search_library(request)
            )
        else:
            search = await self._web_search(request, query)
            library = ToolResult()

        evidence = [*search.evidence, *library.evidence]
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

        for error in [*search.errors, *library.errors]:
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
