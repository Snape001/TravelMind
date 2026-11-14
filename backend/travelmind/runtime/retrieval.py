import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from travelmind.db.evidence import save_evidence
from travelmind.research.engine import ResearchEngine
from travelmind.runtime.execution import RunError
from travelmind.schemas.agents import AgentInput
from travelmind.schemas.domain import Evidence

logger = logging.getLogger(__name__)


class ResearchRetriever:
    def __init__(self, engine: ResearchEngine, sessions: async_sessionmaker[AsyncSession]):
        self.engine = engine
        self.sessions = sessions

    async def __call__(self, request: AgentInput) -> list[Evidence]:
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
            for item in evidence:
                if item.source_type in ("web", "official") and item.source_url:
                    try:
                        # Reserve the rest of the retrieval budget for search and persistence.
                        async with asyncio.timeout(min(12, request.budget.timeout_seconds / 2)):
                            page = await self.engine.scrape(item.source_url)
                    except TimeoutError:
                        logger.warning(
                            "research.scrape_timeout task_id=%s trace_id=%s",
                            request.task_id,
                            request.trace_id,
                        )
                        break
                    except ValueError:
                        continue
                    evidence = [*page.evidence, *evidence]
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
            return await save_evidence(session, request.task_id, evidence)
