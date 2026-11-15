import asyncio
import json
from uuid import UUID, uuid4

import httpx
import pytest
from langgraph.graph import StateGraph

from travelmind.config import Settings
from travelmind.db.worker import PostgresWorkerStore
from travelmind.research.engine import ResearchEngine, ToolResult
from travelmind.runtime.experts import research
from travelmind.runtime.retrieval import ResearchRetriever
from travelmind.schemas.agents import AgentInput, RunBudget

BASE = "https://api.cnkgraph.com"
FIND = "/Api/Book/Find"
VOLUME = "/api/book/volume/KR2k0135_001"
TEXT = """#+PROPERTY: BASEEDITION WYG
<pb:KR2k0135_WYG_001-1a>
　遊天台山日記
癸丑之三月晦自寕海出西門雲散日朗人意山光並有喜態。
<pb:KR2k0135_WYG_001-1b>
別頁正文。
"""
BOOK = {
    "Book": "徐霞客遊記-明-徐宏祖",
    "BookId": "KR2k0135",
    "Volumes": [
        {
            "Volume": "徐霞客遊記 卷一上",
            "VolumeId": "KR2k0135_001",
            "Pages": [
                {
                    "Page": "1-1a",
                    "PreviousText": "卷一上<md:KR2k0135_WYG_001-1a>",
                    "MatchedText": "遊天台山日記",
                    "LaterText": "癸丑之三月晦",
                }
            ],
        }
    ],
}


def search_result():
    return {
        "Count": 2,
        "Error": None,
        "Result": [
            {"Category": "集部 別集類", "Books": [{**BOOK, "Book": "無關文集"}]},
            {"Category": "史部 地理類", "Books": [BOOK, BOOK]},
        ],
    }


def test_reads_original_volume_page_with_bibliographic_reference():
    requests = []

    def respond(request):
        requests.append(request)
        assert "authorization" not in request.headers
        assert request.headers["accept-language"] == "zh-hant"
        if request.url.path == FIND:
            assert request.method == "POST"
            assert json.loads(request.content) == {"key": "游天台山日记", "pageNo": 0}
            return httpx.Response(200, json=search_result())
        assert request.url.path == VOLUME
        assert request.method == "GET"
        return httpx.Response(200, json={"VolumeId": "KR2k0135_001", "Text": TEXT})

    result = run_search(respond)
    assert not result.errors
    assert len(requests) == 2
    (book,) = result.evidence
    assert book.source_type == "book"
    assert str(book.source_url) == BASE + VOLUME
    assert "徐宏祖" in book.source_title
    assert all(term in book.source_locator for term in ("卷一上", "1-1a", "WYG", "知识图谱"))
    assert "雲散日朗" in book.excerpt
    assert "別頁正文" not in book.excerpt
    assert book.published_at is None
    assert book.retrieved_at.tzinfo is not None


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (404, {"Message": "没找到"}, None),
        (200, {"Count": 0, "Result": None}, None),
        (404, {"Message": "route missing"}, "INVALID_RESPONSE"),
        (200, {"Count": 1, "Result": None}, "INVALID_RESPONSE"),
        (200, {"Count": 1, "Result": [], "Error": "upstream error"}, "INVALID_RESPONSE"),
        (403, {}, "ACCESS_DENIED"),
        (429, {}, "RATE_LIMITED"),
    ],
)
def test_empty_malformed_and_access_limited_search(status, body, error):
    def respond(request):
        assert request.url.path == FIND
        return httpx.Response(status, json=body, headers={"Retry-After": "30"})

    result = run_search(respond)
    assert result.evidence == []
    assert [item.code for item in result.errors] == ([error] if error else [])
    assert all(item.provider == "cnkgraph" for item in result.errors)


def test_volume_access_failure_preserves_search_excerpt():
    def respond(request):
        if request.url.path == FIND:
            return httpx.Response(200, json=search_result())
        return httpx.Response(403)

    result = run_search(respond)
    (book,) = result.evidence
    assert "检索摘录" in book.source_locator
    assert "遊天台山日記" in book.excerpt
    assert "<md:" not in book.excerpt


def test_reads_at_most_two_volumes_even_if_more_are_requested():
    calls = []

    def respond(request):
        calls.append(request.url.path)
        if request.url.path == FIND:
            payload = search_result()
            volume = BOOK["Volumes"][0]
            book = {
                **BOOK,
                "Volumes": [
                    {**volume, "VolumeId": f"KR2k0135_{index:03d}"} for index in range(1, 4)
                ],
            }
            payload["Result"] = [{"Category": "史部 地理類", "Books": [book]}]
            return httpx.Response(200, json=payload)
        volume_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"VolumeId": volume_id, "Text": TEXT})

    result = run_search(respond, limit=50)
    assert len(result.evidence) == 2
    assert calls == [FIND, VOLUME, "/api/book/volume/KR2k0135_002"]


def test_cnkgraph_is_disabled_unless_explicitly_enabled():
    def respond(_):
        raise AssertionError("disabled source sent a request")

    result = run_search(respond, enabled=False)
    assert result.evidence == []
    assert [item.code for item in result.errors] == ["NOT_CONFIGURED"]


@pytest.mark.postgres
def test_history_expert_gets_book_evidence_once_and_can_cite_it(client):
    created = client.post("/api/v1/tasks", json={"destination": "游天台山日记", "depth": "deep"})
    assert created.status_code == 201
    task = created.json()
    calls = []

    def respond(request):
        calls.append(request.url.path)
        assert request.url.host == "api.cnkgraph.com"
        if request.url.path == FIND:
            return httpx.Response(200, json=search_result())
        return httpx.Response(200, json={"VolumeId": "KR2k0135_001", "Text": TEXT})

    async def run():
        sessions = client.app.state.sessions
        store = PostgresWorkerStore(sessions)
        claim = await store.claim(uuid4(), lease_seconds=45)
        assert claim is not None and claim.lease.task_id == UUID(task["task_id"])
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
            settings = Settings(_env_file=None, cnkgraph_enabled=True)
            engine = ResearchEngine(transport, settings)

            class EmptyWebSearch:
                async def search(self, query, *, sources, limit):
                    if sources == ("cnkgraph",):
                        return await engine.search(query, sources=sources, limit=limit)
                    return ToolResult()

            search = EmptyWebSearch()
            retrieve = ResearchRetriever(search, sessions, store=store, lease=claim.lease)
            request = AgentInput(
                task_id=task["task_id"],
                trace_id=task["trace_id"],
                run_id=uuid4(),
                agent_id="history_culture",
                brief=task["brief"],
            )
            enabled = ResearchRetriever(
                search, sessions, store=store, lease=claim.lease, library_enabled=True
            )
            assert await retrieve(request) == []
            assert await enabled(request.model_copy(update={"agent_id": "tourism_resources"})) == []
            quick = request.brief.model_copy(update={"depth": "quick"})
            assert await enabled(request.model_copy(update={"brief": quick})) == []
            assert not calls

            async def model(request, instructions, schema, *, context):
                (book,) = request.evidence
                assert "注明记述时代" in instructions
                return schema.model_validate(
                    {
                        "findings": [
                            {
                                "statement": "徐宏祖在游记中记载由宁海西门出发。",
                                "evidence_ids": [book.evidence_id],
                                "support": "supported",
                            }
                        ]
                    }
                )

            async def history(_):
                return {"result": await research(model, enabled, request)}

            graph = StateGraph(dict)
            graph.add_node("history", history)
            graph.set_entry_point("history")
            graph.set_finish_point("history")
            result = (await graph.compile().ainvoke({}))["result"]
            (book,) = result.evidence
            assert result.findings[0].evidence_ids == [book.evidence_id]
            assert await enabled(request) == [book]
            assert calls == [FIND, VOLUME]
            return book

    book = client.portal.call(run)
    stored = client.get(f"{created.headers['location']}/evidence/{book.evidence_id}")
    assert stored.status_code == 200
    assert stored.json()["source_type"] == "book"
    assert stored.json()["source_locator"] == book.source_locator


@pytest.mark.postgres
def test_optional_library_timeout_can_leave_an_empty_result(client):
    created = client.post("/api/v1/tasks", json={"destination": "泉州府", "depth": "deep"})
    assert created.status_code == 201
    task = created.json()

    class SlowLibrary:
        async def search(self, query, *, sources, limit):
            if sources == ("cnkgraph",):
                await asyncio.sleep(5)
                raise AssertionError("optional lookup should be cancelled")
            return ToolResult()

    async def run():
        sessions = client.app.state.sessions
        store = PostgresWorkerStore(sessions)
        claim = await store.claim(uuid4(), lease_seconds=45)
        assert claim is not None and claim.lease.task_id == UUID(task["task_id"])
        retrieve = ResearchRetriever(
            SlowLibrary(), sessions, store=store, lease=claim.lease, library_enabled=True
        )
        request = AgentInput(
            task_id=task["task_id"],
            trace_id=task["trace_id"],
            run_id=uuid4(),
            agent_id="history_culture",
            brief=task["brief"],
            budget=RunBudget(timeout_seconds=0.12),
        )
        assert await retrieve(request) == []

    client.portal.call(run)


def run_search(respond, *, enabled=True, limit=2):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            settings = Settings(_env_file=None, cnkgraph_enabled=enabled)
            engine = ResearchEngine(client, settings)
            return await engine.search("游天台山日记", sources=("cnkgraph",), limit=limit)

    return asyncio.run(run())
