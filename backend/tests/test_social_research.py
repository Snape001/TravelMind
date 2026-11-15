import asyncio
import json
from datetime import UTC, datetime
from time import monotonic, sleep
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from travelmind.config import Settings
from travelmind.db.worker import PostgresWorkerStore
from travelmind.research.engine import ResearchEngine, ToolResult
from travelmind.research.social.adapter import SocialResearchAdapter
from travelmind.research.social.platforms import PLATFORMS, load_export
from travelmind.research.social.schemas import ImportRequest, SocialEvidenceRecord
from travelmind.research.social.service import create_social_app
from travelmind.research.social.zhihu_api import collect
from travelmind.runtime.editorial import write
from travelmind.runtime.experts import find
from travelmind.runtime.planning import plan_research
from travelmind.runtime.retrieval import ResearchRetriever
from travelmind.schemas.agents import AgentInput, ResearchResult
from travelmind.schemas.domain import Evidence

COLLECTED = datetime(2025, 11, 14, tzinfo=UTC)
TOKEN = "test-local-token"


def stage(root, platform="xiaohongshu"):
    key = uuid4()
    batch = root / "inbox" / str(key)
    batch.mkdir(parents=True)
    ids = {
        "xiaohongshu": ("note_id", "6501cd010000000019001234", "desc"),
        "bilibili": ("video_id", "123456", "desc"),
        "weibo": ("note_id", "123456", "content"),
        "douyin": ("aweme_id", "123456", "desc"),
        "zhihu": ("content_id", "456", "content_text"),
    }
    id_field, post_id, text_field = ids[platform]
    rows = [
        {
            id_field: post_id,
            "title": "泉州体验",
            text_field: "周末建议提前预约，门票免费。",
            "time": 1760000000000,
            "create_time": 1760000000,
            "created_time": 1760000000,
            "question_id": "123",
            "content_type": "answer",
            "note_url": "https://www.xiaohongshu.com/explore/fake?xsec_token=secret",
            "nickname": "账号资料",
            "liked_count": "12",
            "voteup_count": 12,
            "video_comment": "40",
        },
    ]
    comments = [
        {id_field: post_id, "comment_id": "1", "content": "周末排队较久，电话13800138000"},
        {
            id_field: post_id,
            "comment_id": "2",
            "parent_comment_id": "1",
            "content": "周二预约后入场快",
        },
        {id_field: post_id, "comment_id": "3", "content": "周二预约后入场快"},
        {id_field: "999", "comment_id": "1", "content": "其他帖子"},
    ]
    if platform == "zhihu":
        for comment in comments:
            comment["content_type"] = "answer"
    for name, values in (("contents", rows * 2), ("comments", comments)):
        (batch / f"{name}.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in values),
            encoding="utf-8",
        )
    return ImportRequest(
        batch_id=key, platform=platform, keyword="泉州 体验", collected_at=COLLECTED
    )


@pytest.mark.parametrize("platform", list(PLATFORMS))
def test_pinned_export_mapping_and_normalized_round_trip(tmp_path, platform):
    request = stage(tmp_path, platform)
    batch = tmp_path / "inbox" / str(request.batch_id)
    (item,), errors = load_export(batch, request)
    assert errors == 0
    assert item.platform == platform and item.published_at.year == 2025
    assert "secret" not in str(item.original_url)
    assert item.author_display_name is None
    assert item.like_count == 12
    if platform == "bilibili":
        assert item.comment_count == 40
    assert len(item.comments) == 2 and item.comments[1].parent_comment_id == "1"
    assert "13800138000" not in item.model_dump_json()
    assert SocialEvidenceRecord.model_validate_json(item.model_dump_json()) == item
    evidence = item.to_evidence()
    assert evidence.evidence_id == item.evidence_id
    assert evidence.retrieved_at == COLLECTED
    assert "周二预约后入场快" in evidence.excerpt
    assert "2 条" in evidence.source_locator


def test_sidecar_jobs_auth_cache_pagination_and_failed_batch(tmp_path):
    app = create_social_app(tmp_path, TOKEN)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        params = {"platform": "bilibili", "keyword": "泉州 体验"}
        assert client.get("/v1/evidence", params=params).status_code == 401
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        request = stage(tmp_path, "bilibili")
        start = client.post("/v1/jobs", json=request.model_dump(mode="json"))
        assert start.status_code == 202
        job_url = start.headers["Location"]
        status = finished_job(client, job_url)
        assert status["status"] == "completed" and status["post_count"] == 1
        assert client.post("/v1/jobs", json=request.model_dump(mode="json")).json() == status
        page = client.get("/v1/evidence", params=params).json()
        assert page["total"] == 1 and len(page["items"]) == 1
        assert client.get("/v1/evidence", params={**params, "offset": 1}).json()["items"] == []
        export = client.get("/v1/evidence", params={**params, "format": "jsonl"})
        assert SocialEvidenceRecord.model_validate_json(export.text.strip()).post_id == "123456"
        missing = request.model_copy(update={"batch_id": uuid4(), "platform": "weibo"})
        failed = client.post("/v1/jobs", json=missing.model_dump(mode="json"))
        status = finished_job(client, failed.headers["Location"])
        assert status["status"] == "failed" and status["error_code"] == "IMPORT_FILE"
        assert client.get("/v1/evidence", params=params).json() == page
        normalized = request.model_copy(update={"batch_id": uuid4(), "format": "normalized"})
        batch = tmp_path / "inbox" / str(normalized.batch_id)
        batch.mkdir()
        (batch / "evidence.jsonl").write_text(export.text, encoding="utf-8")
        replay = client.post("/v1/jobs", json=normalized.model_dump(mode="json"))
        assert finished_job(client, replay.headers["Location"])["status"] == "completed"
        assert client.get("/v1/evidence", params=params).json() == page


def test_sidecar_only_expands_to_matching_destination_when_requested(tmp_path):
    with TestClient(create_social_app(tmp_path, TOKEN), base_url="http://127.0.0.1") as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        request = stage(tmp_path, "zhihu").model_copy(update={"keyword": "泉州 旅游"})
        job = client.post("/v1/jobs", json=request.model_dump(mode="json"))
        assert finished_job(client, job.headers["Location"])["status"] == "completed"
        params = [
            ("platform", "zhihu"), ("keyword", "泉州市 游客体验与口碑"), ("keyword", "泉州市"),
        ]
        assert client.get("/v1/evidence", params=params).json()["total"] == 0
        assert client.get("/v1/evidence", params=[*params, ("destination", "泉州市")]).json()[
            "total"
        ] == 1
        assert client.get("/v1/evidence", params=[*params, ("destination", "福州市")]).json()[
            "total"
        ] == 0


def test_default_platforms_read_three_sources_from_local_cache(tmp_path):
    with TestClient(create_social_app(tmp_path, TOKEN), base_url="http://127.0.0.1") as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        for platform in ("zhihu", "xiaohongshu", "bilibili"):
            request = stage(tmp_path, platform).model_copy(update={"keyword": "泉州 旅游"})
            job = client.post("/v1/jobs", json=request.model_dump(mode="json"))
            assert finished_job(client, job.headers["Location"])["status"] == "completed"

    def refuse_http(_):
        pytest.fail("the file-backed Worker reader should not make HTTP requests")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(refuse_http)) as http:
            settings = Settings(
                _env_file=None,
                social_research_provider="mediacrawler_export",
                social_research_cache_path=tmp_path / "social.sqlite3",
            )
            engine = ResearchEngine(http, settings)
            result = await engine.search_social(["泉州市 游客体验与口碑", "泉州市"])
            assert not result.errors
            assert len(result.evidence) == 3
            assert {item.source_url.host for item in result.evidence} == {
                "www.zhihu.com", "www.xiaohongshu.com", "www.bilibili.com",
            }
            for item in result.evidence:
                assert "周二预约后入场快" in item.excerpt
                has_description = "周末建议提前预约，门票免费。" in item.excerpt
                assert has_description == (item.source_url.host != "www.bilibili.com")
                if not has_description:
                    assert "仅视频评论" in item.source_locator

    asyncio.run(run())


def test_bilibili_without_comments_does_not_reach_expert(tmp_path):
    request = stage(tmp_path, "bilibili")
    (item,), _ = load_export(tmp_path / "inbox" / str(request.batch_id), request)
    item.comments = []

    def respond(_):
        return httpx.Response(200, json={"total": 1, "items": [item.model_dump(mode="json")]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            adapter = SocialResearchAdapter(http, "http://127.0.0.1:18764", TOKEN, 1)
            assert await adapter.search(["泉州"], ("bilibili",)) == ([], [])

    asyncio.run(run())


def test_planner_includes_social_expert_when_selected_in_questionnaire():
    request = AgentInput(
        task_id=uuid4(), trace_id=uuid4(), run_id=uuid4(), agent_id="planner",
        brief={"destination": "泉州市", "topics": ["游客体验与口碑"]},
    )

    async def model(request, instructions, response_type, *, context=None):
        return response_type(
            objective="核对泉州体验",
            assignments=[
                {"agent_id": key, "objective": "核对当地资料"}
                for key in ("history_culture", "natural_geography", "tourism_resources")
            ],
        )

    result = asyncio.run(plan_research(model, request, social_requested=True))
    assert sum(item.agent_id == "social_sentiment" for item in result.plan.assignments) == 1


def finished_job(client, url):
    deadline = monotonic() + 4
    while monotonic() < deadline:
        job = client.get(url).json()
        if job["status"] != "running":
            return job
        sleep(0.015)
    pytest.fail("sidecar import did not finish")


def test_cancel_import_leaves_cache_empty(tmp_path, monkeypatch):
    from travelmind.research.social import service
    from travelmind.research.social.platforms import ImportStopped

    def wait_for_stop(batch, request, stop):
        assert stop.wait(4)
        raise ImportStopped()

    monkeypatch.setattr(service, "load_export", wait_for_stop)
    app = create_social_app(tmp_path, TOKEN)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        request = stage(tmp_path)
        start = client.post("/v1/jobs", json=request.model_dump(mode="json"))
        assert start.status_code == 202
        cancelled = client.delete(start.headers["Location"])
        assert cancelled.json()["status"] == "cancelled"
        params = {"platform": request.platform, "keyword": request.keyword}
        assert client.get("/v1/evidence", params=params).json() == {"items": [], "total": 0}


def test_social_adapter_keeps_available_platform_on_timeout(tmp_path):
    request = stage(tmp_path, "bilibili")
    (item,), _ = load_export(tmp_path / "inbox" / str(request.batch_id), request)

    async def handler(call):
        assert call.headers["Authorization"] == f"Bearer {TOKEN}"
        platform = call.url.params["platform"]
        if platform == "xiaohongshu":
            raise httpx.ReadTimeout("timeout", request=call)
        if platform == "weibo":
            return httpx.Response(503)
        return httpx.Response(200, json={"total": 1, "items": [item.model_dump(mode="json")]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = SocialResearchAdapter(client, "http://127.0.0.1:18764", TOKEN, 1)
            return await adapter.search(["泉州 体验", "泉州"], ("xiaohongshu", "bilibili", "weibo"))

    evidence, errors = asyncio.run(run())
    assert [entry.evidence_id for entry in evidence] == [item.evidence_id]
    assert {error.code for error in errors} == {"TIMEOUT", "UPSTREAM_UNAVAILABLE"}


@pytest.mark.parametrize("platform", ["xiaohongshu", "zhihu"])
def test_social_expert_runs_all_five_skills_for_citable_evidence(tmp_path, platform):
    request = stage(tmp_path, platform)
    (item,), _ = load_export(tmp_path / "inbox" / str(request.batch_id), request)
    agent_request = AgentInput(
        task_id=uuid4(),
        run_id=uuid4(),
        trace_id=uuid4(),
        agent_id="social_sentiment",
        brief={"destination": "泉州"},
        evidence=[item.to_evidence()],
    )

    async def model(request, instructions, response_type, *, context):
        assert "样本" in instructions
        assert "只有独立来源重复支持时才称反复出现" in instructions
        assert request.evidence == [item.to_evidence()]
        assert set(response_type.model_fields) == {
            "travel_sentiment",
            "topic_clustering",
            "complaint_mining",
            "marketing_detection",
            "experience_synthesis",
        }
        return response_type(
            **{
                field: {"statement": "待核对样本局限", "support": "insufficient"}
                for field in response_type.model_fields
            }
        )

    result = asyncio.run(find(model, agent_request, "苏听雨", social_analysis=True))
    assert len(result.findings) == 5


def test_writer_adds_actual_sample_count_time_and_platform(tmp_path):
    request = stage(tmp_path, "bilibili")
    (item,), _ = load_export(tmp_path / "inbox" / str(request.batch_id), request)
    evidence = item.to_evidence()
    writer = AgentInput(
        task_id=uuid4(),
        run_id=uuid4(),
        trace_id=uuid4(),
        agent_id="writer",
        brief={"destination": "泉州"},
        evidence=[evidence],
    )
    contribution = ResearchResult(
        run_id=uuid4(),
        agent_id="social_sentiment",
        kind="research",
        findings=[],
    )

    async def model(request, instructions, response_type, *, context):
        assert "便利样本" in instructions
        return response_type(
            title="泉州",
            sections=[
                {
                    "heading": "社交体验",
                    "body": "样本中有游客表示周末需要排队。",
                    "author_agent_ids": ["social_sentiment"],
                    "evidence_ids": [evidence.evidence_id],
                }
            ],
        )

    report = asyncio.run(write(model, writer, [contribution], None)).report
    body = report.sections[0].body
    assert "B站 1 个视频的 2 条评论或回复" in body
    assert COLLECTED.isoformat() in body
    assert "40 条" not in body


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("platform", "official"),
    [("bilibili", False), ("zhihu", False), ("zhihu", True)],
    ids=["bilibili-export", "zhihu-export", "zhihu-official"],
)
def test_retriever_saves_social_evidence_and_falls_back_without_sidecar(
    client,
    tmp_path,
    platform,
    official,
):
    app = create_social_app(tmp_path, TOKEN)
    with TestClient(app, base_url="http://127.0.0.1") as sidecar:
        sidecar.headers["Authorization"] = f"Bearer {TOKEN}"
        request = stage(tmp_path, platform)
        if official:

            def upstream(endpoint, **params):
                assert endpoint == "content/zhihu_search" and params["Query"] == request.keyword
                return {
                    "Items": [
                        {
                            "ContentID": "456",
                            "ContentType": "Answer",
                            "Url": "https://www.zhihu.com/question/123/answer/456",
                            "Title": "泉州",
                            "ContentText": "测试回答摘要",
                            "AuthorName": "测试作者",
                            "CommentInfoList": [{"Content": "测试精选评论"}],
                        }
                    ]
                }

            (record,) = collect(upstream, "search", request.keyword, COLLECTED)
            request = request.model_copy(update={"format": "normalized"})
            batch = tmp_path / "inbox" / str(request.batch_id)
            (batch / "evidence.jsonl").write_text(record.model_dump_json() + "\n", encoding="utf-8")
        job = sidecar.post("/v1/jobs", json=request.model_dump(mode="json"))
        assert finished_job(sidecar, job.headers["Location"])["status"] == (
            "partial" if official else "completed"
        )
        task = client.post("/api/v1/tasks", json={"destination": "泉州", "topics": ["体验"]}).json()
        agent_request = AgentInput(
            task_id=task["task_id"],
            trace_id=task["trace_id"],
            run_id=uuid4(),
            agent_id="social_sentiment",
            brief=task["brief"],
        )
        website = Evidence(
            evidence_id=uuid4(),
            source_type="official",
            source_title="指南",
            source_url="https://example.com/guide",
            retrieved_at=COLLECTED,
            excerpt="测试指南",
        )

        class WebFallback:
            async def search(self, *args, **kwargs):
                return ToolResult(evidence=[website])

        async def run(social_transport):
            sessions = client.app.state.sessions
            store = PostgresWorkerStore(sessions)
            claim = await store.claim(uuid4(), lease_seconds=30)
            assert claim is not None and claim.lease.task_id == UUID(task["task_id"])
            settings = Settings(
                _env_file=None,
                social_research_provider="mediacrawler_export",
                social_research_token=TOKEN,
                social_research_platforms=(platform,),
            )
            async with (
                httpx.AsyncClient() as web,
                httpx.AsyncClient(transport=social_transport) as local,
            ):
                engine = ResearchEngine(web, settings, social_client=local)
                retrieve = ResearchRetriever(
                    WebFallback(),
                    sessions,
                    store=store,
                    lease=claim.lease,
                    social_search=engine.search_social,
                )
                evidence = await retrieve(agent_request)
                assert await retrieve(agent_request) == evidence
                assert await retrieve(
                    agent_request.model_copy(update={"agent_id": "history_culture"})
                ) == [website]
            async with (
                httpx.AsyncClient() as web,
                httpx.AsyncClient(
                    transport=httpx.MockTransport(lambda _: httpx.Response(503))
                ) as unavailable,
            ):
                engine = ResearchEngine(web, settings, social_client=unavailable)
                fallback = ResearchRetriever(
                    WebFallback(),
                    sessions,
                    store=store,
                    lease=claim.lease,
                    social_search=engine.search_social,
                )
                assert await fallback(agent_request) == [website]
            return evidence

        evidence = client.portal.call(run, httpx.ASGITransport(app=app))
        social, web = evidence
        assert web == website and social.source_type == "social"
        if platform == "zhihu":
            assert str(social.source_url) == "https://www.zhihu.com/question/123/answer/456"
        if official:
            assert "知乎开放平台摘要" in social.source_locator
            assert "测试作者" in social.source_locator and "测试精选评论" in social.excerpt
        saved = client.get(f"/api/v1/tasks/{task['task_id']}/evidence/{social.evidence_id}")
        assert saved.status_code == 200
        assert saved.json()["source_locator"] == social.source_locator
