from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from travelmind.schemas.domain import Evidence
from travelmind.schemas.research_quality import (
    EvaluationCase,
    EvidenceQuality,
    ExpertResearchResult,
    ReflectionAction,
)

AS_OF = datetime(2026, 11, 15, tzinfo=UTC)


@pytest.fixture
def evidence():
    return Evidence(
        evidence_id=uuid4(),
        source_type="official",
        source_title="泉州遗产介绍",
        source_url="https://www.quanzhou.gov.cn/heritage",
        retrieved_at=AS_OF,
        excerpt="2021年，泉州列入《世界遗产名录》。",
    )


@pytest.fixture
def quality_fields(evidence):
    return {
        "evidence_id": evidence.evidence_id,
        "assignment_id": uuid4(),
        "as_of": AS_OF,
        "max_age_days": None,
        "model_id": "test-model",
        "traceability": {"score": 2, "reason": "可公开访问的来源 URL"},
        "authority": {"score": 2, "reason": "泉州政府域名"},
        "freshness": {"score": 2, "reason": "历史事实无时效窗口"},
        "relevance": {"score": 2, "reason": "直接回答列入年份", "quote": evidence.excerpt},
        "directness": {"score": 2, "reason": "原文明确给出年份", "quote": evidence.excerpt},
        "total_score": 10,
        "level": "high",
    }


@pytest.mark.parametrize(
    ("overrides", "score", "level"),
    [
        ({}, 10, "high"),
        (
            {
                "authority": {"score": 1, "reason": "来源未核实"},
                "directness": {"score": 1, "reason": "间接提及", "quote": "泉州"},
                "relevance": {"score": 1, "reason": "部分相关", "quote": "泉州"},
            },
            7,
            "medium",
        ),
        ({"directness": {"score": 0, "reason": "正文未说明"}}, 8, "low"),
        (
            {"max_age_days": 30, "freshness": {"score": 1, "reason": "发布时间缺失"}},
            9,
            "low",
        ),
    ],
)
def test_quality_validates_score_and_grade(quality_fields, overrides, score, level):
    payload = quality_fields | overrides | {"total_score": score, "level": level}
    scorecard = EvidenceQuality.model_validate(payload)
    assert EvidenceQuality.model_validate_json(scorecard.model_dump_json()) == scorecard
    with pytest.raises(ValidationError):
        EvidenceQuality.model_validate(payload | {"total_score": score - 1})
    with pytest.raises(ValidationError):
        EvidenceQuality.model_validate(
            payload | {"level": "medium" if level != "medium" else "low"}
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"relevance": {"score": 2, "reason": "相关但未提供摘录"}},
        {"authority": {"score": 3, "reason": "超出范围"}},
        {"authority": {"score": True, "reason": "布尔值不能作为评分"}},
        {"as_of": AS_OF.replace(tzinfo=None)},
    ],
)
def test_quality_requires_valid_audit_inputs(quality_fields, overrides):
    with pytest.raises(ValidationError):
        EvidenceQuality.model_validate(quality_fields | overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"strategy": "verify_conflict"},
        {"round": 0, "max_rounds": 0},
        {"round": 3},
        {"target_questions": []},
        {"strategy": "stop", "target_questions": []},
        {"strategy": "stop", "stop_reason": "round_limit", "target_questions": []},
        {"strategy": "stop", "stop_reason": "no_new_evidence", "target_questions": []},
        {"state": "conflicting", "strategy": "verify_conflict", "evidence_ids": [uuid4()]},
        {
            "state": "sufficient",
            "strategy": "accept",
            "stop_reason": "sufficient",
            "target_questions": [],
            "model_id": None,
        },
    ],
)
def test_reflection_rejects_inconsistent_routing(overrides):
    fields = {
        "state": "insufficient",
        "strategy": "expand_search",
        "round": 0,
        "max_rounds": 2,
        "reason": "尚缺独立来源",
        "target_questions": ["是否有独立来源？"],
        "model_id": "test-model",
    }
    with pytest.raises(ValidationError):
        ReflectionAction.model_validate(fields | overrides)


def test_expert_result_reuses_research_result_and_checks_references(evidence, quality_fields):
    finding = {
        "statement": "泉州于2021年列入世界遗产名录",
        "support": "supported",
        "evidence_ids": [evidence.evidence_id],
    }
    research = {
        "kind": "research",
        "run_id": uuid4(),
        "agent_id": "history",
        "evidence": [evidence],
        "findings": [finding],
    }
    action = {
        "state": "sufficient",
        "strategy": "accept",
        "stop_reason": "sufficient",
        "round": 0,
        "max_rounds": 0,
        "reason": "原始资料确认列入时间",
        "evidence_ids": [evidence.evidence_id],
        "model_id": "test-model",
    }
    result = {
        "assignment_id": quality_fields["assignment_id"],
        "research": research,
        "quality": [quality_fields],
        "reflection": action,
        "unresolved_questions": [],
    }
    expert_result = ExpertResearchResult.model_validate(result)
    assert (
        ExpertResearchResult.model_validate_json(expert_result.model_dump_json()) == expert_result
    )
    for invalid in [
        {"quality": [quality_fields, quality_fields]},
        {"quality": [quality_fields | {"assignment_id": uuid4()}]},
        {"quality": [quality_fields | {"evidence_id": uuid4()}]},
        {
            "quality": [
                quality_fields | {"directness": quality_fields["directness"] | {"quote": "虚构"}}
            ]
        },
        {"quality": []},
        {"research": research | {"evidence": []}},
        {"reflection": action | {"evidence_ids": [uuid4()]}},
        {"unresolved_questions": ["还未确认列入时间"]},
    ]:
        with pytest.raises(ValidationError):
            ExpertResearchResult.model_validate(result | invalid)


def test_expert_result_can_preserve_a_gap_without_inventing_evidence():
    fields = {
        "assignment_id": uuid4(),
        "research": {"kind": "research", "agent_id": "history", "run_id": uuid4(), "findings": []},
        "quality": [],
        "reflection": {
            "state": "insufficient",
            "strategy": "stop",
            "stop_reason": "round_limit",
            "round": 0,
            "max_rounds": 0,
            "reason": "初次检索未找到资料",
        },
        "unresolved_questions": ["何时对公众开放？"],
    }
    result = ExpertResearchResult.model_validate(fields)
    assert result.unresolved_questions == ["何时对公众开放？"]
    with pytest.raises(ValidationError):
        ExpertResearchResult.model_validate(fields | {"unresolved_questions": []})
    with pytest.raises(ValidationError):
        ExpertResearchResult.model_validate(
            fields
            | {
                "reflection": fields["reflection"]
                | {
                    "strategy": "expand_search",
                    "stop_reason": None,
                    "max_rounds": 1,
                    "target_questions": ["何时对公众开放？"],
                }
            }
        )


def test_evaluation_case_ties_reference_claims_to_reference_evidence(evidence):
    fields = {
        "case_id": uuid4(),
        "brief": {"destination": "泉州", "topics": ["世界遗产"]},
        "question": "泉州何时列入《世界遗产名录》？",
        "as_of": AS_OF,
        "reference_answer": [
            {"statement": "2021年", "support": "supported", "evidence_ids": [evidence.evidence_id]}
        ],
        "reference_evidence": [evidence],
    }
    case = EvaluationCase.model_validate(fields)
    assert EvaluationCase.model_validate_json(case.model_dump_json()) == case
    assert "Evidence" in EvaluationCase.model_json_schema()["$defs"]
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(fields | {"reference_evidence": [evidence, evidence]})
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(
            fields
            | {"reference_answer": [fields["reference_answer"][0] | {"evidence_ids": [uuid4()]}]}
        )
