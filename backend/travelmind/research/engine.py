import asyncio
from collections.abc import Awaitable
from typing import Annotated, Literal, Protocol

import httpx
from pydantic import Field, HttpUrl

from travelmind.config import Settings
from travelmind.research.adapters import BochaSearch, FirecrawlScrape, OpenAlexSearch
from travelmind.research.http import ProviderClient, ProviderError, ProviderFailure
from travelmind.research.sources import is_public_target, unique_sources
from travelmind.schemas.domain import Contract, Evidence, Text

SearchProvider = Literal["bocha", "openalex"]


class SearchRequest(Contract):
    query: Annotated[Text, Field(max_length=1000)]
    sources: tuple[SearchProvider, ...] = Field(min_length=1)
    limit: int = Field(ge=1, le=50)


class ScrapeRequest(Contract):
    url: HttpUrl


class ToolResult(Contract):
    evidence: list[Evidence] = Field(default_factory=list)
    errors: list[ProviderError] = Field(default_factory=list)


class SearchAdapter(Protocol):
    async def search(self, query: str, limit: int) -> list[Evidence]: ...


async def _collect(operation: Awaitable[list[Evidence] | Evidence]) -> ToolResult:
    try:
        items = await operation
    except ProviderFailure as exc:
        return ToolResult(errors=[exc.error])
    return ToolResult(evidence=[items] if isinstance(items, Evidence) else items)


class ResearchEngine:
    """Async tools using a caller-owned HTTP client and the shared Evidence contract."""

    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        timeout = settings.research_timeout_seconds
        self.search_adapters: dict[SearchProvider, SearchAdapter] = {
            "bocha": BochaSearch(ProviderClient(client, "bocha", settings.bocha_api_key, timeout)),
            "openalex": OpenAlexSearch(
                ProviderClient(client, "openalex", settings.openalex_api_key, timeout)
            ),
        }
        self.scraper = FirecrawlScrape(
            ProviderClient(client, "firecrawl", settings.firecrawl_api_key, timeout)
        )

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
                _collect(self.search_adapters[source].search(request.query, request.limit))
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

    async def scrape(self, url: str | HttpUrl) -> ToolResult:
        request = ScrapeRequest(url=url)
        if request.url.username or request.url.password or not is_public_target(request.url):
            raise ValueError("scrape requires a public HTTP(S) URL without credentials")
        return await _collect(self.scraper.scrape(request.url))
