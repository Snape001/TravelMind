from uuid import UUID, uuid4, uuid5

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr
from test_runtime import Model, source

from travelmind.api.dependencies import SESSION_COOKIE
from travelmind.db.evidence import save_evidence
from travelmind.db.tasks import events_after
from travelmind.db.worker import PostgresWorkerStore
from travelmind.research.amap_client import AmapClient
from travelmind.research.amap_poi import AmapPoi
from travelmind.runtime.durable_worker import DurableWorker
from travelmind.runtime.execution import RunError
from travelmind.runtime.intake.questionnaires import (
    build_research_questionnaire,
    build_travel_questionnaire,
    travel_preferences,
)
from travelmind.runtime.intake.worker import IntakeWorker
from travelmind.schemas.domain import ResearchBrief
from travelmind.schemas.phase4_intake import (
    AdministrativeDivisions,
    Destination,
    Gcj02Point,
    QuestionnaireDraft,
    QuestionnaireSubmission,
)

PLACE = {"id": "B001", "name": "开元寺", "location": "118.58,24.91", "cityname": "泉州市"}
DESTINATION = Destination(
    poi_id="B001",
    name="开元寺",
    administrative=AdministrativeDivisions(city="泉州市"),
    coordinates=Gcj02Point(longitude=118.58, latitude=24.91),
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_input_tips_are_resolved_to_pois_and_deduplicated():
    calls = []

    def respond(request):
        calls.append(request.url.path)
        if request.url.path == "/v5/place/text":
            return httpx.Response(200, json={"status": "1", "pois": [dict(PLACE, id=[])]})
        if request.url.path == "/v3/assistant/inputtips":
            assert request.url.params["datatype"] == "poi"
            return httpx.Response(200, json={"status": "1", "tips": [{"id": []}, {"id": "B001"}]})
        assert request.url.params["id"] == "B001"
        return httpx.Response(200, json={"status": "1", "pois": [PLACE, PLACE]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        adapter = AmapPoi(AmapClient(http, SecretStr("fixture")))
        assert await adapter.search("开元伺") == [DESTINATION]
    assert calls == ["/v5/place/text", "/v3/assistant/inputtips", "/v5/place/detail"]


@pytest.mark.anyio
async def test_amap_rate_limit_is_not_an_empty_search():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"status": "0", "infocode": "10003"})
        )
    ) as http:
        with pytest.raises(RunError) as problem:
            await AmapPoi(AmapClient(http, SecretStr("fixture"))).search("泉州")
    assert problem.value.info.code == "AMAP_RATE_LIMIT"
    assert problem.value.info.retryable


@pytest.mark.anyio
async def test_director_reserves_fields_checks_conditions_and_falls_back():
    calls = []

    async def bad_output(request, instructions, response_type, *, context):
        calls.append(request)
        assert response_type is QuestionnaireDraft
        assert context["destination"]["poi_id"] == "B001"
        return QuestionnaireDraft.model_validate(
            {
                "questions": [
                    {"question_id": "poi_id", "text": "填写 ID", "kind": "text", "required": True}
                ]
            }
        )

    questionnaire = await build_research_questionnaire(
        bad_output, uuid4(), uuid4(), ResearchBrief(destination="开元寺"), DESTINATION
    )
    assert len(calls) == 2
    assert all(call.budget.max_output_tokens == 1600 for call in calls)
    assert questionnaire.questions[0].question_id == "interests"
    assert "travel_route" in {opt.option_id for opt in questionnaire.questions[0].options}
    assert questionnaire.questions[-1].question_id == "additional_notes"
    with pytest.raises(ValueError, match="hidden"):
        questionnaire.validate_answers(
            QuestionnaireSubmission.model_validate(
                {"answers": [{"question_id": "culture_focus", "kind": "text", "text": "遗产"}]}
            )
        )


@pytest.mark.anyio
async def test_director_reserves_slots_for_social_and_route_options():
    async def model(*_args, **_kwargs):
        return QuestionnaireDraft.model_validate(
            {
                "questions": [
                    {
                        "question_id": "interests",
                        "text": "调研方向",
                        "kind": "multi_choice",
                        "options": [
                            {"option_id": f"focus_{i}", "label": f"方向 {i}"} for i in range(9)
                        ],
                    },
                    {
                        "question_id": "followup",
                        "text": "想深入核对什么？",
                        "kind": "text",
                        "visible_if": {"question_id": "interests", "includes_option_id": "focus_8"},
                    },
                ],
            }
        )

    questionnaire = await build_research_questionnaire(
        model,
        uuid4(),
        uuid4(),
        ResearchBrief(destination="开元寺"),
        DESTINATION,
    )
    assert [q.question_id for q in questionnaire.questions] == [
        "interests",
        "followup",
        "additional_notes",
    ]
    assert len(questionnaire.questions[0].options) == 10
    assert questionnaire.questions[0].options[-1].option_id == "travel_route"
    assert questionnaire.questions[0].options[-2].option_id == "social_sentiment"
    assert "focus_8" in {option.option_id for option in questionnaire.questions[0].options}


def test_travel_preferences_validate_cross_field_values_and_preserve_zero():
    questionnaire = build_travel_questionnaire()

    def preferences(*answers):
        return travel_preferences(
            questionnaire.validate_answers(
                QuestionnaireSubmission.model_validate({"answers": answers})
            )
        )

    assert preferences() is None
    assert preferences({"question_id": "budget_cny", "kind": "integer", "value": 0}).budget_cny == 0
    with pytest.raises(ValueError):
        preferences(
            {"question_id": "start_date", "kind": "date", "value": "2026-12-16"},
            {"question_id": "end_date", "kind": "date", "value": "2026-12-15"},
        )


@pytest.mark.postgres
@pytest.mark.parametrize("route", [False, True])
def test_intake_http_worker_and_planner_survive_new_claims(client, route):
    planner_inputs = []
    lookups = []
    evidence = source()
    wrong_city = dict(PLACE, id="B002", cityname="潮州市", location="116.64,23.66")

    def amap(request):
        assert request.url.path == "/v5/place/text"
        query = request.url.params["keywords"]
        lookups.append(query)
        pois = [PLACE, wrong_city] if route else [PLACE]
        return httpx.Response(200, json={"status": "1", "pois": pois})

    async def reply(request, _, context):
        if request.agent_id == "planner":
            planner_inputs.append(context)
        if request.agent_id != "research_director":
            return None
        assert context["destination"]["poi_id"] == "B001"
        return {
            "questions": [
                {
                    "question_id": "interests",
                    "text": "调研方向",
                    "kind": "multi_choice",
                    "options": [
                        {"option_id": "culture", "label": "泉州文化"},
                        {"option_id": "visiting", "label": "游览资源"},
                    ],
                },
                {
                    "question_id": "focus",
                    "text": "重点核对的文物",
                    "kind": "text",
                    "visible_if": {"question_id": "interests", "includes_option_id": "culture"},
                },
            ]
        }

    model = Model(evidence, reply=reply)
    saver = InMemorySaver()
    sessions = client.app.state.sessions
    base = "/api/v1/tasks"
    key = str(uuid4())
    raw_destination = " 开元寺 "
    payload = {"destination": raw_destination}

    def run_worker(task_id):
        async def execute():
            store = PostgresWorkerStore(sessions)

            def retrieve(lease):
                async def load(_):
                    async with sessions.begin() as session:
                        await store.check_lease(lease, transaction=session)
                        return await save_evidence(session, lease.task_id, [evidence])

                return load

            async with httpx.AsyncClient(transport=httpx.MockTransport(amap)) as http:
                poi = AmapPoi(AmapClient(http, SecretStr("fixture")))
                worker = DurableWorker(
                    store,
                    model,
                    lambda _: saver,
                    retrieve=retrieve,
                    intake=IntakeWorker(store, poi, model),
                )
                claim = await store.claim(worker.worker_id, lease_seconds=30)
                assert claim.lease.task_id == task_id
                await worker.run_claim(claim)

        client.portal.call(execute)

    created = client.post(
        f"{base}/from-destination", json=payload, headers={"Idempotency-Key": key}
    )
    assert created.status_code == 201
    assert created.json()["brief"] == {
        "destination": "开元寺",
        "topics": [],
        "requirements": "",
        "depth": "quick",
    }
    task_url = created.headers["Location"]
    task_id = UUID(created.json()["task_id"])
    run_worker(task_id)
    first = client.get(f"{task_url}/intake").json()
    confirmation = first["questionnaire"]
    assert confirmation["stage"] == "destination_confirmation"
    assert len(confirmation["candidates"]) == (2 if route else 1)
    assert first["destination"] is None
    assert not model.calls
    assert client.get(f"{task_url}/intake").json() == first
    raw_round = client.get(task_url).json()["pending_clarification"]
    assert raw_round["clarification_id"] == confirmation["questionnaire_id"]
    assert raw_round["questions"][0]["question_id"] == str(
        uuid5(UUID(raw_round["clarification_id"]), confirmation["questions"][0]["question_id"])
    )
    legacy = client.post(
        f"{task_url}/clarifications/{raw_round['clarification_id']}/answer",
        json={
            "answers": [{"question_id": raw_round["questions"][0]["question_id"], "text": "泉州"}]
        },
        headers={"Idempotency-Key": key},
    )
    assert legacy.status_code == 409
    assert legacy.json()["detail"]["code"] == "CLARIFICATION_STALE"

    def submit(round_, answers, idempotency_key=None):
        return client.post(
            f"{task_url}/questionnaires/{round_['questionnaire_id']}/answer",
            json={"answers": answers},
            headers={"Idempotency-Key": idempotency_key or str(uuid4())},
        )

    assert submit(confirmation, []).status_code == 422
    option = next(
        key for key, item in confirmation["candidates"].items() if item["poi_id"] == "B001"
    )
    selected = [{"question_id": "destination", "kind": "single_choice", "option_ids": [option]}]
    assert submit(confirmation, selected).status_code == 202
    assert submit(confirmation, selected).json()["detail"]["code"] == "CLARIFICATION_STALE"
    run_worker(task_id)
    intake = client.get(f"{task_url}/intake").json()
    assert intake["destination"]["poi_id"] == "B001"
    research = intake["questionnaire"]
    assert research["stage"] == "research"
    hidden = [{"question_id": "focus", "kind": "text", "text": "石刻"}]
    assert submit(research, hidden).json()["detail"]["code"] == "INVALID_QUESTIONNAIRE_ANSWERS"
    directions = ["culture", "travel_route"] if route else ["culture"]
    answers = [
        {"question_id": "interests", "kind": "multi_choice", "option_ids": directions},
        *hidden,
        {"question_id": "additional_notes", "kind": "text", "text": " 地方档案 "},
    ]
    accepted = submit(research, answers, key)
    assert accepted.status_code == 202
    answers[0]["option_ids"] = directions[::-1]
    answers.reverse()
    assert submit(research, answers, key).json() == accepted.json()
    run_worker(task_id)
    if route:
        travel = client.get(f"{task_url}/intake").json()["questionnaire"]
        assert travel["stage"] == "travel"
        assert (
            submit(travel, [{"question_id": "start_date", "kind": "date", "value": 0}]).json()[
                "detail"
            ]["code"]
            == "INVALID_QUESTIONNAIRE_ANSWERS"
        )
        assert (
            submit(
                travel,
                [
                    {"question_id": "start_date", "kind": "date", "value": "2026-12-16"},
                    {"question_id": "end_date", "kind": "date", "value": "2026-12-15"},
                ],
            ).json()["detail"]["code"]
            == "INVALID_QUESTIONNAIRE_ANSWERS"
        )
        assert (
            submit(
                travel, [{"question_id": "budget_cny", "kind": "integer", "value": 0}]
            ).status_code
            == 202
        )
        run_worker(task_id)
    assert client.get(task_url).json()["status"] == "completed"
    assert len(planner_inputs) == 1
    planner = planner_inputs[0]["research_intake"]
    assert planner["destination"]["poi_id"] == "B001"
    assert planner["research_questionnaire"] == research
    assert planner["travel_route_requested"] is route
    assert planner["research_submission"]["answers"][0]["text"] == "地方档案"
    assert (
        planner["travel_preferences"]["budget_cny"] if route else planner["travel_preferences"]
    ) == (0 if route else None)
    assert lookups == ["开元寺"]
    replay = client.post(f"{base}/from-destination", json=payload, headers={"Idempotency-Key": key})
    assert replay.status_code == 200
    assert replay.json()["task_id"] == str(task_id)
    assert replay.json()["brief"]["requirements"] == "地方档案"
    legacy = client.post(base, json=payload, headers={"Idempotency-Key": key})
    assert legacy.status_code == 201
    assert legacy.json()["task_id"] != str(task_id)
    assert "event: task.clarification" in client.get(f"{task_url}/events").text
    client.cookies.delete(SESSION_COOKIE)
    assert client.get(f"{task_url}/intake").status_code == 401


@pytest.mark.postgres
def test_empty_poi_search_prompts_refinement_and_retains_input_until_confirmation(client):
    task = client.post("/api/v1/tasks/from-destination", json={"destination": "开元伺"}).json()
    url = f"/api/v1/tasks/{task['task_id']}"
    lookups = []
    sessions = client.app.state.sessions

    class Poi:
        async def search(self, keyword):
            lookups.append(keyword)
            return [DESTINATION] if keyword == "泉州 开元寺" else []

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("no model before destination confirmation")

    async def advance():
        store = PostgresWorkerStore(sessions)
        worker = IntakeWorker(store, Poi(), forbidden)
        lease = await store.claim(uuid4(), lease_seconds=30)
        assert await worker.prepare(lease) == (False, None)

    client.portal.call(advance)
    question = client.get(f"{url}/intake").json()["questionnaire"]
    assert question["stage"] == "destination_search"
    answer = {"question_id": "destination_query", "kind": "text", "text": "泉州 开元寺"}
    result = client.post(
        f"{url}/questionnaires/{question['questionnaire_id']}/answer",
        json={"answers": [answer]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert result.status_code == 202
    assert result.json()["brief"]["destination"] == "开元伺"
    client.portal.call(advance)
    assert (
        client.get(f"{url}/intake").json()["questionnaire"]["stage"] == "destination_confirmation"
    )
    assert lookups == ["开元伺", "泉州 开元寺"]


@pytest.mark.postgres
def test_amap_configuration_failure_is_visible_in_existing_event_stream(client):
    task = client.post("/api/v1/tasks/from-destination", json={"destination": "开元寺"}).json()
    task_id = UUID(task["task_id"])
    sessions = client.app.state.sessions

    async def unavailable(*_args, **_kwargs):
        raise AssertionError("no call expected")

    async def failure_payload():
        store = PostgresWorkerStore(sessions)
        async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as http:
            worker = IntakeWorker(store, AmapPoi(AmapClient(http, None)), unavailable)
            claim = await store.claim(uuid4(), lease_seconds=30)
            assert await worker.prepare(claim) == (False, None)
        async with sessions.begin() as session:
            return (await events_after(session, task_id, 0))[-1].payload

    error = client.portal.call(failure_payload)
    assert error.status == "failed"
    assert error.error.code == "AMAP_NOT_CONFIGURED"
    assert client.get(f"/api/v1/tasks/{task_id}/intake").json()["questionnaire"] is None
