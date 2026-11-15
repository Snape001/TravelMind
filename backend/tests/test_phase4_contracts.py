import json
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from travelmind.schemas.domain import TaskStatus
from travelmind.schemas.events import AgentEvent
from travelmind.schemas.phase4_events import AgentUsage, ResearchBrowserEvent
from travelmind.schemas.phase4_experts import (
    ExpertProfile,
    ExpertSkill,
    ExpertSkillInvocation,
    ExpertSkillResult,
)
from travelmind.schemas.phase4_intake import (
    Destination,
    DestinationTaskCreate,
    QuestionnaireDraft,
    QuestionnaireSubmission,
    ResearchIntakeContext,
    ResearchIntakeRead,
    ResearchQuestionnaire,
    TravelPreferences,
)
from travelmind.schemas.phase4_openapi import phase4_components
from travelmind.schemas.phase4_sources import ResearchSourceRead, SocialEvidence, TravelRoute
from travelmind.schemas.task_control import next_task_status


@pytest.fixture
def contract_examples():
    path = Path(__file__).resolve().parents[2] / "fixtures" / "phase4.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("key", "model"),
    [
        ("create", DestinationTaskCreate),
        ("destination", Destination),
        ("destination_questionnaire", ResearchQuestionnaire),
        ("research_questionnaire", ResearchQuestionnaire),
        ("travel_questionnaire", ResearchQuestionnaire),
        ("travel_preferences", TravelPreferences),
        ("expert_profile", ExpertProfile),
        ("skill", ExpertSkill),
        ("skill_invocation", ExpertSkillInvocation),
        ("skill_result", ExpertSkillResult),
        ("intake", ResearchIntakeRead),
        ("source", ResearchSourceRead),
        ("route", TravelRoute),
        ("social", SocialEvidence),
    ],
)
def test_shared_fixture_serializes(key, model, contract_examples):
    instance = model.model_validate(contract_examples[key])
    assert model.model_validate_json(instance.model_dump_json()) == instance


def test_home_accepts_only_destination():
    assert DestinationTaskCreate(destination=" 泉州 ").destination == "泉州"
    with pytest.raises(ValidationError, match="Extra inputs"):
        DestinationTaskCreate.model_validate({"destination": "泉州", "topics": []})


def test_coordinates_preserve_amap_crs(contract_examples):
    raw = contract_examples["destination"]
    raw["coordinates"]["crs"] = "wgs84"
    with pytest.raises(ValidationError, match="gcj02"):
        Destination.model_validate(raw)


def test_destination_options_must_match_verified_candidates(contract_examples):
    raw = contract_examples["destination_questionnaire"]
    raw["candidates"] = {}
    with pytest.raises(ValidationError, match="candidate"):
        ResearchQuestionnaire.model_validate(raw)


def test_questionnaire_accepts_skipping_optional_inputs(contract_examples):
    questionnaire = ResearchQuestionnaire.model_validate(
        contract_examples["research_questionnaire"]
    )
    assert questionnaire.validate_answers(QuestionnaireSubmission(answers=[])) == {}
    answers = QuestionnaireSubmission.model_validate(contract_examples["research_answers"])
    assert set(questionnaire.validate_answers(answers)) == {"interests", "travel_focus"}


def test_questionnaire_rejects_hidden_answer_and_unknown_choice(contract_examples):
    questionnaire = ResearchQuestionnaire.model_validate(
        contract_examples["research_questionnaire"]
    )
    raw = contract_examples["research_answers"]
    raw["answers"][0]["option_ids"] = ["history"]
    with pytest.raises(ValueError, match="hidden"):
        questionnaire.validate_answers(QuestionnaireSubmission.model_validate(raw))
    raw["answers"][0]["option_ids"] = ["unknown"]
    with pytest.raises(ValueError, match="choices"):
        questionnaire.validate_answers(QuestionnaireSubmission.model_validate(raw))


def test_questionnaire_rejects_forward_condition_and_llm_supplied_poi(contract_examples):
    raw = contract_examples["research_questionnaire"]
    questions = raw["questions"]
    with pytest.raises(ValidationError, match="earlier"):
        QuestionnaireDraft.model_validate({"questions": [questions[1], questions[0]]})
    with pytest.raises(ValidationError, match="Extra inputs"):
        QuestionnaireDraft.model_validate({"questions": questions, "candidates": {}})


def test_stale_and_duplicate_questionnaire_answers(contract_examples):
    questionnaire = ResearchQuestionnaire.model_validate(
        contract_examples["research_questionnaire"]
    )
    raw = contract_examples["research_answers"]
    raw["answers"].append(
        {"question_id": "old-question", "kind": "text", "text": "from the previous round"}
    )
    with pytest.raises(ValueError, match="unknown"):
        questionnaire.validate_answers(QuestionnaireSubmission.model_validate(raw))
    raw["answers"][-1] = raw["answers"][0]
    with pytest.raises(ValidationError, match="once"):
        QuestionnaireSubmission.model_validate(raw)


def test_travel_preferences_allow_omission_but_reject_reversed_dates():
    assert TravelPreferences().transport_modes is None
    with pytest.raises(ValidationError, match="end_date"):
        TravelPreferences.model_validate({"start_date": "2026-12-03", "end_date": "2026-12-01"})


def test_planner_context_rejects_preferences_without_route_request(contract_examples):
    context = {
        "destination": contract_examples["destination"],
        "research_questionnaire": contract_examples["research_questionnaire"],
        "research_submission": contract_examples["research_answers"],
        "travel_route_requested": True,
        "travel_preferences": contract_examples["travel_preferences"],
    }
    validated = ResearchIntakeContext.model_validate(context)
    assert validated.research_submission.answers[-1].question_id == "travel_focus"
    context["research_submission"] = {"answers": []}
    with pytest.raises(ValidationError, match="route request"):
        ResearchIntakeContext.model_validate(context)
    context["travel_route_requested"] = False
    with pytest.raises(ValidationError, match="preferences"):
        ResearchIntakeContext.model_validate(context)


def test_sidecar_does_not_add_a_v1_agent_event_or_task_status(contract_examples):
    events = list(map(ResearchBrowserEvent.model_validate, contract_examples["research_events"]))
    assert [event.sequence for event in events] == list(range(1, 7))
    assert events[-1].payload.type == "agent.usage"
    for event in events:
        with pytest.raises(ValidationError, match="union_tag_invalid"):
            AgentEvent.model_validate_json(event.model_dump_json())
    assert get_args(TaskStatus) == (
        "queued",
        "running",
        "needs_clarification",
        "paused",
        "completed",
        "failed",
    )
    assert next_task_status("running", "clarification_required") == "needs_clarification"
    assert next_task_status("needs_clarification", "answer") == "queued"


def test_usage_requires_reported_integer_counts_or_null(contract_examples):
    usage = contract_examples["research_events"][-1]["payload"]["usage"]
    assert AgentUsage.model_validate(usage).input_tokens == 620
    usage.update(input_tokens=None, output_tokens=0)
    assert AgentUsage.model_validate(usage).input_tokens is None
    usage["input_tokens"] = True
    with pytest.raises(ValidationError):
        AgentUsage.model_validate(usage)


def test_component_export_contains_the_wire_discriminators():
    exported = phase4_components()
    schemas = exported["components"]["schemas"]
    assert exported["paths"] == {}
    payload = schemas["ResearchBrowserEvent"]["properties"]["payload"]
    assert payload["discriminator"]["propertyName"] == "type"
    for ref in payload["discriminator"]["mapping"].values():
        assert ref == f"#/components/schemas/{ref.rsplit('/', 1)[1]}"
        assert ref.rsplit("/", 1)[1] in schemas
