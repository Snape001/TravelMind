import asyncio
from collections.abc import Awaitable
from typing import Annotated, Literal, Protocol

import httpx
from pydantic import Field, HttpUrl

from travelmind.config import Settings
from travelmind.research.adapters import BochaSearch, FirecrawlScrape, OpenAlexSearch
from travelmind.research.cnkgraph import CnkgraphBooks
from travelmind.research.http import ProviderClient, ProviderError, ProviderFailure, failure
from travelmind.research.social.adapter import SocialResearchAdapter
from travelmind.research.social.schemas import Platform
from travelmind.research.sources import is_public_target, unique_sources
from travelmind.schemas.domain import Contract, Evidence, Text

SearchProvider = Literal["bocha", "openalex", "cnkgraph"]


class SearchRequest(Contract):
    query: Annotated[Text, Field(max_length=1000)]
    sources: tuple[SearchProvider, ...] = Field(min_length=1)
    limit: int = Field(ge=1, le=50)


class ToolResult(Contract):
    evidence: list[Evidence] = Field(default_factory=list)
    errors: list[ProviderError] = Field(default_factory=list)
    body_text: str | None = None


class SearchAdapter(Protocol):
    async def search(self, query: str, limit: int) -> list[Evidence]: ...


async def _collect(operation: Awaitable[list[Evidence]]) -> ToolResult:
    try:
        items = await operation
    except ProviderFailure as exc:
        return ToolResult(errors=[exc.error])
    return ToolResult(evidence=items)


class ResearchEngine:
    """Async tools using a caller-owned HTTP client and the shared Evidence contract."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: Settings,
        *,
        social_client: httpx.AsyncClient | None = None,
    ):
        timeout = settings.research_timeout_seconds
        self.library_enabled = settings.cnkgraph_enabled
        self.search_adapters: dict[SearchProvider, SearchAdapter] = {
            "bocha": BochaSearch(ProviderClient(client, "bocha", settings.bocha_api_key, timeout)),
            "openalex": OpenAlexSearch(
                ProviderClient(client, "openalex", settings.openalex_api_key, timeout)
            ),
        }
        if self.library_enabled:
            self.search_adapters["cnkgraph"] = CnkgraphBooks(
                ProviderClient(client, "cnkgraph", None, min(timeout, 3))
            )
        self.scraper = FirecrawlScrape(
            ProviderClient(client, "firecrawl", settings.firecrawl_api_key, timeout)
        )
        self.social = None
        self.social_platforms = settings.social_research_platforms
        token = settings.social_research_token
        cache_path = settings.social_research_cache_path
        if settings.social_research_provider == "mediacrawler_export" and (token or cache_path):
            self.social = SocialResearchAdapter(
                social_client or client,
                str(settings.social_research_url),
                token.get_secret_value() if token else "",
                settings.social_research_timeout_seconds,
                cache_path=cache_path,
            )

    async def search_social(
        self,
        keywords: list[str],
        *,
        platforms: tuple[Platform, ...] | None = None,
        timeout: float | None = None,
    ) -> ToolResult:
        if self.social is None:
            return ToolResult()
        items, errors = await self.social.search(
            keywords,
            self.social_platforms if platforms is None else platforms,
            timeout=timeout,
        )
        return ToolResult(evidence=items, errors=errors)

    async def search(
        self,
        query: str,
        *,
        sources: tuple[SearchProvider, ...] = ("bocha", "openalex"),
        limit: int = 10,
    ) -> ToolResult:
        """Search the selected providers concurrently, with a limit per provider."""
        request = SearchRequest(query=query, sources=sources, limit=limit)
        batches = await asyncio.gather(
            *(
                _collect(self._search(source, request.query, request.limit))
                for source in dict.fromkeys(request.sources)
            )
        )
        return ToolResult(
            evidence=unique_sources(
                item
                for batch in batches
                for item in unique_sources(batch.evidence)[: request.limit]
            ),
            errors=[error for batch in batches for error in batch.errors],
        )

    async def _search(self, source: SearchProvider, query: str, limit: int) -> list[Evidence]:
        adapter = self.search_adapters.get(source)
        if adapter is None:
            raise failure(source, "NOT_CONFIGURED", "古籍检索尚未启用")
        return await adapter.search(query, limit)

    async def scrape(self, url: str | HttpUrl) -> ToolResult:
        target = HttpUrl(url)
        if not is_public_target(target):
            raise ValueError("scrape requires a public HTTP(S) URL without credentials")
        try:
            evidence, body = await self.scraper.scrape(target)
        except ProviderFailure as exc:
            return ToolResult(errors=[exc.error])
        return ToolResult(evidence=[evidence], body_text=body)
