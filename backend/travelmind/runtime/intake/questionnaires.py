import logging
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from travelmind.runtime.execution import InvalidOutput, RunError, with_budget
from travelmind.runtime.model import StructuredModel
from travelmind.runtime.planning import DIRECTOR
from travelmind.schemas.agents import AgentInput, RunBudget
from travelmind.schemas.domain import ResearchBrief
from travelmind.schemas.phase4_intake import (
    ChoiceAnswer,
    Destination,
    QuestionnaireAnswer,
    QuestionnaireDraft,
    QuestionnaireOption,
    QuestionnaireQuestion,
    ResearchQuestionnaire,
    TravelPreferences,
)

logger = logging.getLogger(__name__)
ROUTE = QuestionnaireOption(option_id="travel_route", label="旅游路线")
NOTES = QuestionnaireQuestion(
    question_id="additional_notes", text="还有哪些个性化需求？", kind="text"
)
QUESTIONNAIRE_BUDGET = RunBudget(timeout_seconds=15, max_attempts=2, max_output_tokens=1600)


def _options(labels: dict[str, str]) -> list[QuestionnaireOption]:
    return [QuestionnaireOption(option_id=key, label=label) for key, label in labels.items()]


def build_destination_questionnaire(candidates: list[Destination]) -> ResearchQuestionnaire:
    if not candidates:
        return ResearchQuestionnaire(
            questionnaire_id=uuid4(),
            stage="destination_search",
            questions=[
                QuestionnaireQuestion(
                    question_id="destination_query",
                    text="没有找到匹配的地点。请补上城市或区县，或检查名称后重新搜索。",
                    kind="text",
                    required=True,
                )
            ],
        )
    index = {
        f"poi_{uuid5(NAMESPACE_URL, f'amap:{candidate.poi_id}').hex}": candidate
        for candidate in candidates
    }
    return ResearchQuestionnaire(
        questionnaire_id=uuid4(),
        stage="destination_confirmation",
        questions=[
            QuestionnaireQuestion(
                question_id="destination",
                text="你要调研的是哪一个地点？",
                kind="single_choice",
                required=True,
                options=_options({key: item.name for key, item in index.items()}),
            )
        ],
        candidates=index,
    )


def _fallback() -> QuestionnaireDraft:
    return QuestionnaireDraft.model_validate(
        {
            "questions": [
                {
                    "question_id": "interests",
                    "text": "你想深入了解哪些方向？",
                    "kind": "multi_choice",
                    "options": _options(
                        {
                            "history_culture": "历史与地方文化",
                            "natural_geography": "自然景观与地理",
                            "tourism_resources": "景点与游览资源",
                            "social_sentiment": "游客体验与口碑",
                        }
                    ),
                },
                {
                    "question_id": "culture_focus",
                    "text": "你特别关注哪段历史、哪项遗产或哪类地方文化？",
                    "kind": "text",
                    "visible_if": {
                        "question_id": "interests",
                        "includes_option_id": "history_culture",
                    },
                },
                {
                    "question_id": "nature_focus",
                    "text": "你对哪些自然景观、季节特征或生态议题感兴趣？",
                    "kind": "text",
                    "visible_if": {
                        "question_id": "interests",
                        "includes_option_id": "natural_geography",
                    },
                },
                {
                    "question_id": "sites_focus",
                    "text": "你想重点了解哪些景点或游览限制？",
                    "kind": "text",
                    "visible_if": {
                        "question_id": "interests",
                        "includes_option_id": "tourism_resources",
                    },
                },
                {
                    "question_id": "experience_focus",
                    "text": "你想核实哪些游客体验或口碑分歧？",
                    "kind": "text",
                    "visible_if": {
                        "question_id": "interests",
                        "includes_option_id": "social_sentiment",
                    },
                },
            ]
        }
    )


def _publish_research(draft: QuestionnaireDraft) -> ResearchQuestionnaire:
    interests, *followups = draft.questions
    if (
        interests.question_id != "interests"
        or interests.kind != "multi_choice"
        or interests.visible_if is not None
        or not 2 <= len(interests.options) <= 9
        or ROUTE.option_id in {option.option_id for option in interests.options}
        or not 1 <= len(followups) <= 5
        or any(question.required for question in draft.questions)
        or any(
            question.visible_if is None or question.question_id == NOTES.question_id
            for question in followups
        )
    ):
        raise InvalidOutput("research questions must follow the intake template")
    return ResearchQuestionnaire(
        questionnaire_id=uuid4(),
        stage="research",
        questions=[
            interests.model_copy(update={"options": [*interests.options, ROUTE]}),
            *followups,
            NOTES,
        ],
    )


async def build_research_questionnaire(
    model: StructuredModel,
    task_id: UUID,
    trace_id: UUID,
    brief: ResearchBrief,
    destination: Destination,
) -> ResearchQuestionnaire:
    request = AgentInput(
        task_id=task_id,
        trace_id=trace_id,
        run_id=uuid5(task_id, "intake:research"),
        agent_id=DIRECTOR,
        brief=brief,
        budget=QUESTIONNAIRE_BUDGET,
    )

    async def generate() -> ResearchQuestionnaire:
        draft = await model(
            request,
            "担任 Research Director。按已确认地点的类别、行政区和调研深度设计调研问题。"
            "第一题固定为 interests，kind 为 multi_choice。给出 2–9 个方向选项，建议 3–5 个。"
            "再生成 1–5 个简短选填追问，每题 visible_if 引用前面题目的一个真实选项。"
            "使用短英文 ID，所有问题 required=false。不要生成旅游路线选项 travel_route、"
            "additional_notes、出行表单或地点标识，应用会补齐这些字段。"
            "不要编造目的地属性或把未经核实的结论写成问题前提。",
            QuestionnaireDraft,
            context={"destination": destination.model_dump(mode="json")},
        )
        return _publish_research(draft)

    try:
        return await with_budget(generate, request.budget)
    except RunError as exc:
        logger.warning("intake.questionnaire_fallback task_id=%s code=%s", task_id, exc.info.code)
        return _publish_research(_fallback())


def build_travel_questionnaire() -> ResearchQuestionnaire:
    return ResearchQuestionnaire(
        questionnaire_id=uuid4(),
        stage="travel",
        questions=[
            QuestionnaireQuestion(question_id="start_date", text="出发日期", kind="date"),
            QuestionnaireQuestion(question_id="end_date", text="返程日期", kind="date"),
            QuestionnaireQuestion(question_id="stay_days", text="停留天数", kind="integer"),
            QuestionnaireQuestion(
                question_id="transport_modes",
                text="交通方式",
                kind="multi_choice",
                options=_options(
                    {"driving": "驾车", "transit": "公共交通", "walking": "步行", "cycling": "骑行"}
                ),
            ),
            QuestionnaireQuestion(
                question_id="styles",
                text="旅行风格",
                kind="multi_choice",
                options=_options(
                    {
                        "culture": "人文漫游",
                        "nature": "自然探索",
                        "food": "寻味",
                        "photography": "摄影",
                        "family": "亲子体验",
                    }
                ),
            ),
            QuestionnaireQuestion(
                question_id="intensity",
                text="行程强度",
                kind="single_choice",
                options=_options({"relaxed": "松弛", "balanced": "适中", "intensive": "紧凑"}),
            ),
            QuestionnaireQuestion(
                question_id="budget_cny", text="整团预算（人民币元）", kind="integer"
            ),
            QuestionnaireQuestion(
                question_id="party_size", text="同行人数（含自己）", kind="integer"
            ),
            QuestionnaireQuestion(
                question_id="companions",
                text="同行者类别",
                kind="multi_choice",
                options=_options(
                    {
                        "solo": "独行",
                        "partner": "伴侣",
                        "friends": "朋友",
                        "children": "儿童",
                        "seniors": "长辈",
                    }
                ),
            ),
        ],
    )


def travel_preferences(answers: dict[str, QuestionnaireAnswer]) -> TravelPreferences | None:
    values = {
        key: (answer.option_ids[0] if answer.kind == "single_choice" else answer.option_ids)
        if isinstance(answer, ChoiceAnswer)
        else answer.value
        for key, answer in answers.items()
        if answer.kind != "text"
    }
    return TravelPreferences.model_validate(values) if values else None
