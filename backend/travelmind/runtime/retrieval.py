import asyncio
import logging
import re
from collections import defaultdict
from typing import Protocol
from uuid import UUID

from pydantic import HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from travelmind.db.evidence import save_evidence
from travelmind.db.research_browser import source_id_for
from travelmind.research.engine import ResearchEngine, SearchProvider, ToolResult
from travelmind.research.http import failure
from travelmind.research.social.skills import social_keywords
from travelmind.research.sources import is_public_target, url_key
from travelmind.runtime.execution import RunError
from travelmind.runtime.research_telemetry import current_telemetry
from travelmind.schemas.agents import AgentInput
from travelmind.schemas.domain import Evidence
from travelmind.schemas.worker import LeaseLost, LeaseRef, WorkerStore

logger = logging.getLogger(__name__)
HISTORY_TOPICS = {"历史文化", "历史与地方文化", "地方文化", "地方史", "文化遗产", "民俗"}


class SocialSearch(Protocol):
    async def __call__(self, keywords: list[str], *, timeout: float) -> ToolResult: ...


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
        social_search: SocialSearch | None = None,
        library_enabled: bool = False,
    ):
        self.engine = engine
        self.sessions = sessions
        self.store = store
        self.lease = lease
        self.social_search = social_search
        self.library_enabled = library_enabled
        self._library_lock = asyncio.Lock()
        self._library_result: ToolResult | None = None
        self._scrape_locks = defaultdict(asyncio.Lock)
        self._scrape_results: dict[tuple[str, str], ToolResult] = {}

    async def _scrape(self, url: HttpUrl, request: AgentInput, source_id: UUID) -> ToolResult:
        key = url_key(url)
        telemetry = current_telemetry.get()
        attempt = None
        try:
            # Keep the scrape and any wait for a parallel expert inside the same budget.
            async with asyncio.timeout(min(12, request.budget.timeout_seconds / 2)):
                async with self._scrape_locks[key]:
                    if key in self._scrape_results:
                        return self._scrape_results[key]
                    if telemetry is not None:
                        attempt = await telemetry.start_fetch(request, source_id)
                    page = await self.engine.scrape(url)
                    if telemetry is not None:
                        await telemetry.finish_fetch(
                            request,
                            source_id,
                            attempt,
                            body=page.body_text,
                            error_code=None
                            if page.evidence
                            else (page.errors[0].code if page.errors else "EMPTY_CONTENT"),
                        )
                    if page.evidence:
                        self._scrape_results[key] = page
                    return page
        except TimeoutError:
            logger.warning(
                "research.scrape_timeout task_id=%s trace_id=%s",
                request.task_id,
                request.trace_id,
            )
            if telemetry is not None and attempt is not None:
                await telemetry.finish_fetch(
                    request, source_id, attempt, body=None, error_code="SCRAPE_TIMEOUT"
                )
            return ToolResult()

    async def _web(self, request: AgentInput) -> tuple[ToolResult, dict[UUID, UUID]]:
        brief = request.brief
        query = research_query(request)
        sources: tuple[SearchProvider, ...] = (
            ("bocha", "openalex") if brief.depth == "academic" else ("bocha",)
        )
        search = await self.engine.search(query, sources=sources, limit=3)
        if not search.evidence and "openalex" not in sources:
            fallback = await self.engine.search(query, sources=("openalex",), limit=3)
            search.evidence.extend(fallback.evidence)
            search.errors.extend(fallback.errors)
        if telemetry := current_telemetry.get():
            await telemetry.discover(request, search.evidence)

        aliases = {}
        if brief.depth != "quick":
            for index, item in enumerate(search.evidence):
                url = item.source_url
                if item.source_type in ("web", "official") and url and is_public_target(url):
                    source_id = source_id_for(request.task_id, item)
                    page = await self._scrape(url, request, source_id)
                    if page.evidence:
                        aliases = {
                            source_id_for(request.task_id, found): source_id
                            for found in page.evidence
                        }
                        search.evidence = [
                            *page.evidence,
                            *search.evidence[:index],
                            *search.evidence[index + 1 :],
                        ]
                    search.errors.extend(page.errors)
                    break
        return search, aliases

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
        supplemental = ToolResult()
        if self.social_search is not None and request.agent_id == "social_sentiment":
            (search, aliases), supplemental = await asyncio.gather(
                self._web(request),
                self.social_search(
                    social_keywords(request), timeout=request.budget.timeout_seconds / 4
                ),
            )
        elif (
            self.library_enabled
            and request.agent_id == "history_culture"
            and request.brief.depth != "quick"
        ):
            (search, aliases), supplemental = await asyncio.gather(
                self._web(request), self._search_library(request)
            )
        else:
            search, aliases = await self._web(request)
        evidence = [*supplemental.evidence, *search.evidence]
        if not evidence and search.errors:
            problem = search.errors[-1]
            raise RunError(problem.code, problem.message, retryable=problem.retryable)
        for error in [*search.errors, *supplemental.errors]:
            logger.warning(
                "research.partial task_id=%s trace_id=%s provider=%s code=%s",
                request.task_id,
                request.trace_id,
                error.provider,
                error.code,
            )
        async with self.sessions.begin() as session:
            await self.store.check_lease(self.lease, transaction=session)
            saved = await save_evidence(session, request.task_id, evidence)
            if telemetry := current_telemetry.get():
                await telemetry.link_evidence(session, request, saved, aliases)
            return saved
