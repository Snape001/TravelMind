from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr
from sqlalchemy import update
from test_expert_reflection import Scenario
from test_runtime import source

from travelmind.api.dependencies import SESSION_COOKIE
from travelmind.db.auth import start_session
from travelmind.db.evidence import save_evidence
from travelmind.db.models import TaskRow
from travelmind.db.tasks import events_after
from travelmind.db.worker import PostgresWorkerStore
from travelmind.research.amap_client import AmapClient
from travelmind.research.amap_poi import AmapPoi
from travelmind.research.amap_routes import AmapRoutes
from travelmind.runtime.durable_worker import DurableWorker
from travelmind.runtime.intake.worker import IntakeWorker
from travelmind.runtime.planning import PlanDraft
from travelmind.runtime.travel.map_cache import AmapResultCache
from travelmind.runtime.travel.places import AmapPlaces
from travelmind.runtime.travel.planner import TravelPlanner
from travelmind.schemas.domain import ReportDraft
from travelmind.schemas.map_data import MapData
from travelmind.schemas.phase4_intake import QuestionnaireDraft
from travelmind.schemas.worker import LeaseLost, MapProjectionBatch


@pytest.mark.postgres
@pytest.mark.parametrize("mode", ["present", "missing", "not_requested"])
def test_typed_worker_projects_map_and_publishes_report(client, mode):
    requested = mode != "not_requested"
    source_item = source()
    scenario = Scenario(source_item)
    route_calls, tool_calls, writer_sources = [], [], []
    dropped = None
    saver = InMemorySaver()
    sessions = client.app.state.sessions

    class InterruptedProjection(Exception):
        pass

    class Store(PostgresWorkerStore):
        async def commit_projection(self, lease, projection):
            nonlocal dropped
            if mode == "present" and isinstance(projection, MapProjectionBatch) and dropped is None:
                dropped = (lease, projection)
                raise InterruptedProjection
            return await super().commit_projection(lease, projection)

    async def model(request, instructions, response_type, *, context=None):
        if response_type is QuestionnaireDraft:
            return QuestionnaireDraft(
                questions=[
                    {
                        "question_id": "interests",
                        "text": "方向",
                        "kind": "multi_choice",
                        "options": [{"option_id": "history", "label": "历史"}],
                    }
                ]
            )
        if requested and response_type is PlanDraft:
            return PlanDraft(
                objective="旅游规划",
                assignments=[
                    {"agent_id": agent, "objective": objective}
                    for agent, objective in (
                        ("history_culture", "整理历史"),
                        ("tourism_resources", "核对资源"),
                        ("social_sentiment", "核对体验"),
                    )
                ],
            )
        if response_type is ReportDraft:
            maps = [item for item in request.evidence if item.source_type == "map"]
            writer_sources.extend(maps)
            if maps:
                return ReportDraft(
                    title="规划报告",
                    sections=[
                        {
                            "heading": "路线",
                            "body": maps[0].excerpt,
                            "author_agent_ids": ["travel_planning"],
                            "evidence_ids": [maps[0].evidence_id],
                        }
                    ],
                )
        return await scenario.model(request, instructions, response_type, context=context)

    places = [
        {
            "id": f"P{i}",
            "name": name,
            "cityname": "泉州市",
            "adname": "鲤城区",
            "citycode": "0595",
            "adcode": "350502",
            "location": f"118.{585000 + i * 1000},24.91",
        }
        for i, name in enumerate(("开元寺", "甲公园", "乙公园", "丙公园"))
    ]

    def provider(request):
        path, params = request.url.path, request.url.params
        tool_calls.append(path)
        if path.endswith("/place/text"):
            body = {"pois": places if params["keywords"] == "景点" else places[:1]}
        elif path.endswith("/place/detail"):
            body = {"pois": [poi for poi in places if poi["id"] == params["id"]]}
        else:
            route_calls.append(path)
            if mode == "missing":
                return httpx.Response(200, json={"status": "0", "infocode": "10021"})
            start, end = params["origin"], params["destination"]
            if path.endswith("/walking"):
                body = {
                    "route": {
                        "paths": [
                            {
                                "distance": "800",
                                "cost": {"duration": "900"},
                                "steps": [{"polyline": f"{start};{end}"}],
                            }
                        ]
                    }
                }
            else:
                body = {
                    "route": {
                        "transits": [
                            {
                                "distance": "1000",
                                "cost": {"duration": "420"},
                                "segments": [
                                    {
                                        "bus": {
                                            "buslines": [
                                                {
                                                    "id": "L002",
                                                    "name": "测试公交",
                                                    "departure_stop": {
                                                        "id": "S1",
                                                        "name": "上车站",
                                                        "location": start,
                                                    },
                                                    "arrival_stop": {
                                                        "id": "S2",
                                                        "name": "下车站",
                                                        "location": end,
                                                    },
                                                    "polyline": {"polyline": f"{start};{end}"},
                                                }
                                            ]
                                        }
                                    }
                                ],
                            }
                        ]
                    }
                }
        return httpx.Response(200, json={"status": "1", **body})

    made = client.post("/api/v1/tasks/from-destination", json={"destination": "开元寺"})
    assert made.status_code == 201
    task_id, base = UUID(made.json()["task_id"]), made.headers["Location"]

    async def run_worker():
        store = Store(sessions)

        def retrieve(lease):
            async def load(request):
                async with sessions.begin() as session:
                    await store.check_lease(lease, transaction=session)
                    return await save_evidence(session, request.task_id, [source_item])

            return load

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            amap = AmapClient(http, SecretStr("fixture"))
            cache = AmapResultCache(amap)
            worker = DurableWorker(
                store,
                model,
                lambda _: saver,
                retrieve=retrieve,
                reflection=scenario.config(),
                intake=IntakeWorker(store, AmapPoi(amap), model),
                travel_planner=TravelPlanner(AmapPlaces(cache), AmapRoutes(cache)),
            )
            claim = await store.claim(worker.worker_id, lease_seconds=30)
            assert claim.lease.task_id == task_id
            await worker.run_claim(claim)

    def answer(stage, answers):
        questionnaire = client.get(f"{base}/intake").json()["questionnaire"]
        assert questionnaire["stage"] == stage
        if stage == "destination_confirmation":
            answers = [
                {
                    "question_id": "destination",
                    "kind": "single_choice",
                    "option_ids": [next(iter(questionnaire["candidates"]))],
                }
            ]
        result = client.post(
            f"{base}/questionnaires/{questionnaire['questionnaire_id']}/answer",
            json={"answers": answers},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert result.status_code == 202, result.json()

    client.portal.call(run_worker)
    answer("destination_confirmation", [])
    client.portal.call(run_worker)
    answer(
        "research",
        [
            {
                "question_id": "interests",
                "kind": "multi_choice",
                "option_ids": ["travel_route"],
            }
        ]
        if requested
        else [],
    )
    if requested:
        client.portal.call(run_worker)
        answer(
            "travel",
            [
                {"question_id": "start_date", "kind": "date", "value": "2026-12-06"},
                {"question_id": "stay_days", "kind": "integer", "value": 2},
                {
                    "question_id": "transport_modes",
                    "kind": "multi_choice",
                    "option_ids": ["walking", "transit"],
                },
                {"question_id": "intensity", "kind": "single_choice", "option_ids": ["relaxed"]},
            ],
        )
    if mode == "present":
        with pytest.raises(InterruptedProjection):
            client.portal.call(run_worker)
        assert dropped and not client.get(f"{base}/routes").json()["routes"]
        calls_at_checkpoint = len(route_calls)

        async def expire():
            async with sessions.begin() as session:
                await session.execute(
                    update(TaskRow)
                    .where(TaskRow.task_id == task_id)
                    .values(
                        lease_until=datetime.now(UTC) - timedelta(seconds=60),
                    )
                )

        client.portal.call(expire)
        client.portal.call(run_worker)
        assert len(route_calls) == calls_at_checkpoint
        with pytest.raises(LeaseLost):
            client.portal.call(Store(sessions).commit_projection, *dropped)
    else:
        client.portal.call(run_worker)
    assert client.get(base).json()["status"] == "completed"
    checkpoint = client.portal.call(saver.aget_tuple, {"configurable": {"thread_id": str(task_id)}})
    assert checkpoint.metadata["graph_version"] == "durable-v3"

    async def load_events():
        async with sessions() as session:
            return await events_after(session, task_id, 0)

    events = client.portal.call(load_events)
    report_id = next(
        event.payload.report_id for event in events if event.payload.type == "report.ready"
    )
    map_url = f"{base}/reports/{report_id}/map-data"
    data = MapData.model_validate(client.get(map_url).json())
    assert data.task_id == task_id and data.report_id == report_id
    stored_routes = client.get(f"{base}/routes").json()["routes"]
    assert stored_routes == [item.model_dump(mode="json") for item in data.selected_routes()]
    plan = next(event.payload.plan for event in events if event.payload.type == "plan.ready")
    assert any(a.agent_id == "travel_planning" for a in plan.assignments) is requested
    if mode == "present":
        assert data.status == "complete"
        assert len(data.days) == len(stored_routes) == 2
        assert {entry.route.mode for entry in data.routes} == {"walking", "transit"}
        assert all(
            entry.segments[0].departure_stop.name == "上车站"
            for entry in data.routes
            if entry.route.mode == "transit"
        )
        assert "/v5/place/detail" in tool_calls and writer_sources
        for evidence in writer_sources:
            stored = client.get(f"{base}/evidence/{evidence.evidence_id}")
            assert stored.status_code == 200 and stored.json()["source_type"] == "map"
    elif mode == "missing":
        assert data.status == "partial" and stored_routes == []
        assert route_calls and any(issue.code == "AMAP_RATE_LIMIT" for issue in data.issues)
        report = client.get(f"{base}/reports/{report_id}").json()["content"]
        assert any("地图数据不完整" in section["body"] for section in report["sections"])
    else:
        assert data.status == "unavailable" and not route_calls
        assert tool_calls == ["/v5/place/text"]

    async def another_user():
        async with sessions.begin() as session:
            return await start_session(
                session,
                "https://issuer.example",
                str(uuid4()),
                None,
                lifetime=60,
                old_token=None,
            )

    client.cookies.set(SESSION_COOKIE, client.portal.call(another_user))
    assert client.get(map_url).status_code == 404
    assert client.get(f"{base}/routes").status_code == 404
