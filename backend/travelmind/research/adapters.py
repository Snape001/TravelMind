from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import HttpUrl

from travelmind.research.http import ProviderClient, failure, invalid_response
from travelmind.research.sources import source_url, web_source_type
from travelmind.schemas.domain import Evidence

EXCERPT_LIMIT = 20_000


def text_value(value: object) -> str | None:
    if isinstance(value, str):
        return value.replace("\x00", "").encode("utf-8", errors="replace").decode().strip() or None
    if isinstance(value, list):
        for part in value:
            if isinstance(part, str) and (clean := text_value(part)) is not None:
                return clean
    return None


def publication_date(value: object) -> datetime | None:
    raw = text_value(value)
    if raw is None:
        return None
    try:
        date = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return date if date.tzinfo else date.replace(tzinfo=UTC)


def abstract_text(index: object) -> str | None:
    if not isinstance(index, dict):
        return None
    words = {
        position: clean
        for word, positions in index.items()
        if isinstance(positions, list) and (clean := text_value(word)) is not None
        for position in positions
        if type(position) is int and position >= 0
    }
    return " ".join(words[position] for position in sorted(words)) or None


class BochaSearch:
    def __init__(self, http: ProviderClient):
        self.http = http

    async def search(self, query: str, limit: int) -> list[Evidence]:
        payload = await self.http.request(
            "POST",
            "https://api.bocha.cn/v1/web-search",
            json={"query": query, "count": limit, "freshness": "noLimit", "summary": True},
            status_field="code",
        )
        data = payload.get("data")
        pages = data.get("webPages") if isinstance(data, dict) else None
        if isinstance(data, dict) and pages is None:
            return []
        items = pages.get("value") if isinstance(pages, dict) else None
        if not isinstance(items, list):
            raise invalid_response("bocha")

        retrieved_at = datetime.now(UTC)
        results = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = source_url(item.get("url"))
            excerpt = text_value(item.get("summary")) or text_value(item.get("snippet"))
            if url is None or excerpt is None:
                continue
            results.append(
                Evidence(
                    evidence_id=uuid4(),
                    source_type=web_source_type(url),
                    source_title=text_value(item.get("name")) or str(url),
                    source_url=url,
                    source_locator=text_value(item.get("siteName")),
                    published_at=publication_date(item.get("datePublished")),
                    retrieved_at=retrieved_at,
                    excerpt=excerpt[:EXCERPT_LIMIT].rstrip(),
                )
            )
        return results


class OpenAlexSearch:
    def __init__(self, http: ProviderClient):
        self.http = http

    async def search(self, query: str, limit: int) -> list[Evidence]:
        payload = await self.http.request(
            "GET",
            "https://api.openalex.org/works",
            params={
                "search": query,
                "per_page": limit,
                "select": (
                    "id,doi,title,display_name,publication_date,primary_location,"
                    "abstract_inverted_index"
                ),
            },
        )
        items = payload.get("results")
        if not isinstance(items, list):
            raise invalid_response("openalex")

        retrieved_at = datetime.now(UTC)
        results = []
        for item in items:
            if not isinstance(item, dict):
                continue
            record = source_url(item.get("id"))
            location = item.get("primary_location")
            landing_page = location.get("landing_page_url") if isinstance(location, dict) else None
            url = source_url(item.get("doi")) or source_url(landing_page) or record
            title = text_value(item.get("title")) or text_value(item.get("display_name"))
            if url is None or title is None:
                continue
            excerpt = abstract_text(item.get("abstract_inverted_index")) or title
            results.append(
                Evidence(
                    evidence_id=uuid4(),
                    source_type="academic",
                    source_title=title,
                    source_url=url,
                    source_locator=str(record) if record else None,
                    published_at=publication_date(item.get("publication_date")),
                    retrieved_at=retrieved_at,
                    excerpt=excerpt[:EXCERPT_LIMIT].rstrip(),
                )
            )
        return results


class FirecrawlScrape:
    def __init__(self, http: ProviderClient):
        self.http = http

    async def scrape(self, url: HttpUrl) -> Evidence:
        payload = await self.http.request(
            "POST",
            "https://api.firecrawl.dev/v2/scrape",
            json={
                "url": str(url).split("#", 1)[0],
                "formats": ["markdown"],
                "onlyMainContent": True,
            },
        )
        if payload.get("success") is False:
            raise failure("firecrawl", "SCRAPE_FAILED", "Firecrawl 未能提取网页正文")
        data = payload.get("data")
        if payload.get("success") is not True or not isinstance(data, dict):
            raise invalid_response("firecrawl")
        metadata: dict[str, Any] = data.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        status = metadata.get("statusCode")
        if type(status) is int and not 200 <= status < 300:
            raise failure(
                "firecrawl",
                "PAGE_UNAVAILABLE",
                f"目标网页不可用（状态 {status}）",
                retryable=status in (408, 429) or 500 <= status <= 599,
            )
        markdown = text_value(data.get("markdown"))
        if markdown is None:
            raise failure("firecrawl", "EMPTY_CONTENT", "Firecrawl 没有返回网页正文")
        reported_url = metadata.get("url") or metadata.get("sourceURL")
        final_url = source_url(reported_url) if reported_url is not None else url
        if final_url is None:
            raise invalid_response("firecrawl")
        return Evidence(
            evidence_id=uuid4(),
            source_type=web_source_type(final_url),
            source_title=text_value(metadata.get("title")) or str(final_url),
            source_url=final_url,
            source_locator=text_value(metadata.get("ogSiteName")),
            published_at=publication_date(
                metadata.get("article:published_time") or metadata.get("publishedTime")
            ),
            retrieved_at=datetime.now(UTC),
            excerpt=markdown[:EXCERPT_LIMIT].rstrip(),
        )
