import asyncio
import json
import sys
from datetime import timedelta

import httpx
import pytest
from pydantic import ValidationError

from travelmind.config import Settings
from travelmind.research.engine import ResearchEngine


def make_engine(handler, **settings):
    config = Settings(_env_file=None, **settings)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client, ResearchEngine(client, config)


def test_bocha_search_deduplicates_and_preserves_source_fields():
    def respond(request):
        assert request.method == "POST"
        assert request.url == "https://api.bocha.cn/v1/web-search"
        assert request.headers["authorization"] == "Bearer bocha-key"
        assert json.loads(request.content) == {
            "query": "泉州古城",
            "count": 5,
            "freshness": "noLimit",
            "summary": True,
        }
        results = [
            {
                "name": "泉州古城",
                "url": "https://www.quanzhou.gov.cn/guide?lang=zh&utm_source=search#intro",
                "summary": "这是一段详细摘要。",
                "snippet": "简短摘要",
                "siteName": "泉州市人民政府",
                "datePublished": "2025-05-04T18:00:00+08:00",
            },
            {
                "name": "重复页面",
                "url": "https://www.quanzhou.gov.cn/guide?lang=zh",
                "snippet": "重复结果",
            },
            {
                "name": "其他语言",
                "url": "https://www.quanzhou.gov.cn/guide?lang=en",
                "snippet": "English introduction",
                "datePublished": "invalid",
            },
            {"name": "坏链接", "url": "javascript:alert(1)", "snippet": "无效"},
            {"name": "内部地址", "url": "http://127.0.0.1/", "snippet": "无效"},
            {"name": "无正文", "url": "https://example.org/empty"},
        ]
        return httpx.Response(200, json={"code": 200, "data": {"webPages": {"value": results}}})

    async def run():
        client, engine = make_engine(respond, bocha_api_key="bocha-key")
        async with client:
            return await engine.search("  泉州古城  ", sources=("bocha",), limit=5)

    result = asyncio.run(run())
    assert result.errors == []
    first, second = result.evidence
    assert first.source_type == "official"
    assert first.source_title == "泉州古城"
    assert first.source_locator == "泉州市人民政府"
    assert (
        str(first.source_url) == "https://www.quanzhou.gov.cn/guide?lang=zh&utm_source=search#intro"
    )
    assert first.published_at.utcoffset() == timedelta(hours=8)
    assert first.retrieved_at.utcoffset() == timedelta(0)
    assert first.excerpt == "这是一段详细摘要。"
    assert second.published_at is None
    assert second.excerpt == "English introduction"


def test_openalex_reconstructs_abstract_and_handles_sparse_records():
    def respond(request):
        assert request.url.host == "api.openalex.org"
        assert request.url.path == "/works"
        assert request.url.params["search"] == "heritage tourism Quanzhou"
        assert request.url.params["per_page"] == "3"
        assert request.headers["authorization"] == "Bearer openalex-key"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "doi": "https://doi.org/10.1000/X1",
                        "title": "Heritage tourism",
                        "publication_date": "2024-01-20",
                        "abstract_inverted_index": {
                            "matters": [2],
                            "Heritage": [0],
                            "tourism": [1],
                        },
                    },
                    {
                        "id": "https://openalex.org/W2",
                        "doi": "http://dx.doi.org/10.1000/x1",
                        "title": "Duplicate DOI",
                    },
                    {
                        "id": "https://openalex.org/W3",
                        "display_name": "Another paper",
                        "primary_location": {"landing_page_url": "https://example.org/article"},
                        "abstract_inverted_index": None,
                    },
                    {"id": "http://127.0.0.1/", "title": "Invalid source"},
                ]
            },
        )

    async def run():
        client, engine = make_engine(respond, openalex_api_key="openalex-key")
        async with client:
            return await engine.search("heritage tourism Quanzhou", sources=("openalex",), limit=3)

    result = asyncio.run(run())
    assert result.errors == []
    first, sparse = result.evidence
    assert first.source_type == "academic"
    assert str(first.source_url) == "https://doi.org/10.1000/X1"
    assert first.source_locator == "https://openalex.org/W1"
    assert first.published_at.isoformat() == "2024-01-20T00:00:00+00:00"
    assert first.excerpt == "Heritage tourism matters"
    assert str(sparse.source_url) == "https://example.org/article"
    assert sparse.excerpt == "Another paper"


def test_firecrawl_returns_markdown_and_final_url():
    def respond(request):
        assert request.url == "https://api.firecrawl.dev/v2/scrape"
        assert request.headers["authorization"] == "Bearer firecrawl-key"
        assert json.loads(request.content) == {
            "url": "https://example.org/guide",
            "formats": ["markdown"],
            "onlyMainContent": True,
        }
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "markdown": "# 到达方式\n\n乘坐公交。",
                    "metadata": {
                        "sourceURL": "https://example.org/guide",
                        "url": "https://example.org/zh/guide",
                        "title": ["旅行指南"],
                        "ogSiteName": "旅游网站",
                        "article:published_time": "2025-05-02T09:00:00+08:00",
                        "statusCode": 200,
                    },
                },
            },
        )

    async def run():
        client, engine = make_engine(respond, firecrawl_api_key="firecrawl-key")
        async with client:
            return await engine.scrape("https://example.org/guide#bus")

    result = asyncio.run(run())
    assert result.errors == []
    (evidence,) = result.evidence
    assert str(evidence.source_url) == "https://example.org/zh/guide"
    assert evidence.source_title == "旅行指南"
    assert evidence.source_locator == "旅游网站"
    assert evidence.published_at.utcoffset() == timedelta(hours=8)
    assert evidence.excerpt == "# 到达方式\n\n乘坐公交。"


def test_parallel_search_keeps_results_when_other_provider_is_rate_limited():
    attempts = 0

    async def run():
        openalex_started = asyncio.Event()

        async def respond(request):
            nonlocal attempts
            if request.url.host == "api.bocha.cn":
                await asyncio.wait_for(openalex_started.wait(), timeout=1)
                attempts += 1
                return httpx.Response(429, headers={"Retry-After": "0"})
            assert "authorization" not in request.headers
            openalex_started.set()
            return httpx.Response(
                200, json={"results": [{"id": "https://openalex.org/W9", "title": "A paper"}]}
            )

        client, engine = make_engine(respond, bocha_api_key="bocha-key")
        async with client:
            return await engine.search("泉州")

    result = asyncio.run(run())
    assert attempts == 2
    assert result.evidence[0].source_title == "A paper"
    (error,) = result.errors
    assert (error.provider, error.code, error.retryable) == ("bocha", "RATE_LIMITED", True)


def test_parallel_search_deduplicates_doi_across_providers():
    def respond(request):
        if request.url.host == "api.bocha.cn":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "webPages": {
                            "value": [
                                {
                                    "url": "http://dx.doi.org/10.1000/Example",
                                    "name": "Search result",
                                    "snippet": "A snippet",
                                },
                            ],
                        },
                    },
                },
            )
        return httpx.Response(
            200,
            json={"results": [{"doi": "https://doi.org/10.1000/example", "title": "An article"}]},
        )

    async def run():
        client, engine = make_engine(respond, bocha_api_key="bocha-key")
        async with client:
            return await engine.search("example", sources=("openalex", "bocha"))

    result = asyncio.run(run())
    assert result.errors == []
    (evidence,) = result.evidence
    assert evidence.source_type == "academic"
    assert evidence.source_title == "An article"


def test_timeouts_are_bounded_and_reported_after_retry():
    attempts = 0

    async def respond(_):
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(10)
        pytest.fail("expected request to time out")

    async def run():
        client, engine = make_engine(respond, research_timeout_seconds=0.01)
        async with client:
            return await asyncio.wait_for(engine.search("泉州", sources=("openalex",)), timeout=2)

    result = asyncio.run(run())
    assert attempts == 2
    assert result.evidence == []
    (error,) = result.errors
    assert (error.code, error.retryable) == ("TIMEOUT", True)


@pytest.mark.parametrize(
    ("reply", "code", "retryable"),
    [
        (httpx.Response(200, json={"code": 401}), "AUTHENTICATION_FAILED", False),
        (
            httpx.Response(200, json={"code": 200, "data": {"webPages": []}}),
            "INVALID_RESPONSE",
            False,
        ),
        (httpx.Response(200, content=b"html"), "INVALID_RESPONSE", False),
        (httpx.Response(429, headers={"Retry-After": "120"}), "RATE_LIMITED", True),
    ],
)
def test_fails_without_retrying_permanent_errors_or_long_rate_limits(reply, code, retryable):
    requests = []

    def respond(request):
        requests.append(request)
        return reply

    async def run():
        client, engine = make_engine(respond, bocha_api_key="bocha-key")
        async with client:
            return await engine.search("泉州", sources=("bocha",))

    result = asyncio.run(run())
    assert len(requests) == 1
    assert result.evidence == []
    assert result.errors[0].code == code
    assert result.errors[0].retryable == retryable


def test_firecrawl_reports_target_page_failure_without_storing_error_page():
    def respond(_):
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {"markdown": "Not found", "metadata": {"statusCode": 404}},
            },
        )

    async def run():
        client, engine = make_engine(respond, firecrawl_api_key="firecrawl-key")
        async with client:
            return await engine.scrape("https://example.org/missing")

    result = asyncio.run(run())
    assert result.evidence == []
    assert result.errors[0].code == "PAGE_UNAVAILABLE"


def test_firecrawl_rejects_non_public_reported_final_url():
    def respond(_):
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "markdown": "A page",
                    "metadata": {
                        "sourceURL": "https://example.org/guide",
                        "url": "http://127.0.0.1/",
                    },
                },
            },
        )

    async def run():
        client, engine = make_engine(respond, firecrawl_api_key="firecrawl-key")
        async with client:
            return await engine.scrape("https://example.org/guide")

    result = asyncio.run(run())
    assert result.evidence == []
    assert result.errors[0].code == "INVALID_RESPONSE"


@pytest.mark.parametrize(
    "content",
    [
        b"[" * (sys.getrecursionlimit() + 200) + b"]" * (sys.getrecursionlimit() + 200),
        b'{"results":[],"padding":"' + b" " * (8 * 1024 * 1024) + b'"}',
    ],
)
def test_openalex_refuses_invalid_or_oversized_payload_without_hiding_bocha_evidence(content):
    def respond(request):
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, content=content)
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "webPages": {
                        "value": [{"url": "https://example.org/", "summary": "Travel evidence"}],
                    },
                },
            },
        )

    async def run():
        client, engine = make_engine(respond, bocha_api_key="bocha-key")
        async with client:
            return await engine.search("泉州")

    result = asyncio.run(run())
    assert [item.excerpt for item in result.evidence] == ["Travel evidence"]
    assert [(error.provider, error.code) for error in result.errors] == [
        ("openalex", "INVALID_RESPONSE")
    ]


def test_bocha_provider_text_is_safe_to_persist_and_serialize():
    def respond(_):
        return httpx.Response(
            200,
            content=json.dumps(
                {
                    "code": 200,
                    "data": {
                        "webPages": {
                            "value": [
                                {
                                    "name": "\x00",
                                    "url": "https://example.org/",
                                    "summary": "Travel\x00 evidence \ud800",
                                }
                            ],
                        },
                    },
                }
            ).encode(),
        )

    async def run():
        client, engine = make_engine(respond, bocha_api_key="bocha-key")
        async with client:
            return await engine.search("泉州", sources=("bocha",))

    result = asyncio.run(run())
    (item,) = result.evidence
    assert item.source_title == "https://example.org/"
    assert item.excerpt == "Travel evidence ?"
    item.model_dump_json()


def test_validates_inputs_and_missing_credentials_before_sending_requests():
    def respond(_):
        pytest.fail("unexpected provider request")

    async def run():
        client, engine = make_engine(respond, bocha_api_key="")
        async with client:
            with pytest.raises(ValidationError):
                await engine.search(" ")
            with pytest.raises(ValidationError):
                await engine.search("泉州", limit=51)
            with pytest.raises(ValidationError):
                await engine.scrape("file:///etc/passwd")
            with pytest.raises(ValueError):
                await engine.scrape("http://127.0.0.1/")
            search = await engine.search("泉州", sources=("bocha",))
            scrape = await engine.scrape("https://example.org/")
            assert search.errors[0].code == scrape.errors[0].code == "NOT_CONFIGURED"

    asyncio.run(run())
