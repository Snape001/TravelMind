import json
from pathlib import Path
from subprocess import run
from sys import executable
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter

from travelmind.evaluation.__main__ import markdown
from travelmind.evaluation.contracts import (
    CandidateRun,
    Dataset,
    Judgment,
    RunBatch,
    fingerprint,
)
from travelmind.evaluation.scoring import compare, evaluate, score_run
from travelmind.schemas.domain import Evidence

DATA = Path(__file__).resolve().parents[1] / "evaluation_data"


@pytest.fixture
def dataset():
    return Dataset.model_validate_json((DATA / "cases.json").read_bytes())


@pytest.fixture
def case(dataset):
    return dataset.cases[0]


@pytest.fixture
def candidate(dataset, case):
    reference = dataset.reference(case)
    hit = reference.reference_evidence[0].model_copy(update={"evidence_id": uuid4()})
    duplicate = Evidence.model_validate(
        hit.model_dump() | {"evidence_id": uuid4(), "source_url": f"{hit.source_url}?utm_source=x"}
    )
    unrelated = Evidence.model_validate(
        hit.model_dump() | {"evidence_id": uuid4(), "source_url": "https://example.org/other"}
    )
    missing = uuid4()
    return CandidateRun(
        case_key=case.key,
        task_id=uuid4(),
        origin="recorded",
        source="test recording",
        status="completed",
        evidence=[hit, duplicate, unrelated],
        ranked_evidence_ids=[hit.evidence_id, duplicate.evidence_id, unrelated.evidence_id],
        report={
            "title": "评测样例",
            "sections": [
                {
                    "heading": "莫高窟",
                    "body": "莫高窟于1987年列入世界遗产。莫高窟每天开放到午夜。",
                    "author_agent_ids": ["history_culture"],
                    "evidence_ids": [hit.evidence_id, duplicate.evidence_id, missing],
                }
            ],
        },
        usage={"input_tokens": 0, "output_tokens": 10, "model_calls": 2, "duration_ms": 500},
    )


def test_dataset_has_two_destinations_and_requires_human_signoff(dataset):
    assert dataset.inventory() == {"敦煌": {"pending": 18}, "泉州": {"pending": 18}}
    references = [dataset.reference(case) for case in dataset.cases]
    assert len({case.case_id for case in references}) == 36
    assert all(case.question in case.brief.requirements for case in references)
    with pytest.raises(ValueError, match="human review"):
        Dataset.model_validate(
            dataset.model_dump() | {"cases": [dataset.cases[0].model_dump() | {"status": "gold"}]}
        )


def test_rank_uses_sources_not_uuids_and_duplicate_ranks_cannot_inflate_precision(
    dataset, case, candidate
):
    result = score_run(dataset, case, candidate, [1, 2, 5])
    assert result["metrics"]["recall@1"]["value"] == 1
    assert result["metrics"]["precision@2"]["value"] == 0.5
    assert result["metrics"]["precision@5"]["value"] == 0.2
    assert result["evidence"]["duplicate_source_records"] == 1
    assert result["metrics"]["citation_resolution"]["value"] == 2 / 3
    assert result["metrics"]["citation_accuracy"]["value"] is None


def make_judgment(dataset, case, candidate):
    return Judgment(
        case_key=case.key,
        task_id=candidate.task_id,
        run_sha256=fingerprint(candidate),
        case_sha256=fingerprint(dataset.reference(case)),
        judge_id="test-judge-v1",
        judge_type="model",
        inventory_complete=True,
        claims=[
            {
                "section": 0,
                "quote": "莫高窟于1987年列入世界遗产。",
                "verdict": "supported",
                "supporting_evidence_ids": [candidate.evidence[0].evidence_id],
                "reason": "UNESCO 资料明确写明列入年份。",
            },
            {
                "section": 0,
                "quote": "莫高窟每天开放到午夜。",
                "verdict": "unsupported",
                "reason": "截取的资料只介绍遗产历史。",
            },
        ],
        coverage=[{"reference": 0, "verdict": "covered", "claim": 0, "reason": "年份一致。"}],
    )


def test_semantic_metrics_keep_denominators_and_source_links(dataset, case, candidate):
    judgment = make_judgment(dataset, case, candidate)
    result = score_run(dataset, case, candidate, [1], judgment)
    assert result["metrics"]["faithfulness"]["value"] == 0.5
    assert result["metrics"]["citation_accuracy"]["value"] == 1 / 3
    assert result["metrics"]["completeness"]["value"] == 1
    assert result["coverage"][0]["reference_evidence_ids"]
    assert result["coverage"][0]["candidate_evidence_ids"] == [
        str(candidate.evidence[0].evidence_id)
    ]
    judgment.claims[0].supporting_evidence_ids = [uuid4()]
    with pytest.raises(ValueError, match="resolved citation"):
        score_run(dataset, case, candidate, [1], judgment)


def test_missing_observations_are_distinct_from_zero_and_failures(dataset, case, candidate):
    failed = CandidateRun(
        case_key=case.key,
        task_id=uuid4(),
        origin="recorded",
        source="test recording",
        status="failed",
        failure_code="REVIEW_REJECTED",
        ranked_evidence_ids=[],
    )
    batch = RunBatch(target_commit="a" * 40, runs=[candidate, failed])
    report = evaluate(dataset, batch, [1])
    pending = report["summary"]["recorded"]["pending"]
    assert pending["task_success_rate"]["value"] == 0.5
    assert pending["telemetry"]["input_tokens"] == {"observed": 1, "total": 0, "mean": 0}
    assert pending["telemetry"]["total_tokens"]["total"] == 10
    assert report["results"][1]["metrics"]["recall@1"]["value"] == 0
    assert report["results"][1]["metrics"]["faithfulness"]["value"] is None
    assert report["results"][1]["failure_code"] == "REVIEW_REJECTED"
    assert "gold" not in report["summary"]["recorded"]


def test_rejects_judgment_reused_after_candidate_changes(dataset, case, candidate):
    judgment = make_judgment(dataset, case, candidate)
    changed = candidate.model_copy(update={"task_id": UUID(int=1)})
    with pytest.raises(ValueError, match="snapshot"):
        score_run(dataset, case, changed, [1], judgment)


def test_gold_results_require_a_signed_case_and_get_their_own_summary(dataset, case, candidate):
    approved = {
        "status": "gold",
        "human_review": {
            "reviewer": "test reviewer",
            "verified_at": "2026-11-15T23:00:00+08:00",
            "notes": "test-only human review metadata",
        },
    }
    reviewed = Dataset.model_validate(
        dataset.model_dump() | {"cases": [case.model_dump() | approved]}
    )
    batch = RunBatch(target_commit="a" * 40, runs=[candidate])
    result = evaluate(reviewed, batch, [1])
    assert set(result["summary"]["recorded"]) == {"gold"}
    assert result["inventory"] == {"敦煌": {"gold": 1}}


def test_optional_phase_three_reflection_is_checked_against_case_depth(dataset, case, candidate):
    snapshot = candidate.model_dump()
    snapshot["expert_research"] = [
        {
            "assignment_id": str(uuid4()),
            "research": {
                "run_id": str(uuid4()),
                "agent_id": "history_culture",
                "kind": "research",
                "findings": [{"statement": "开放信息待查", "support": "insufficient"}],
            },
            "quality": [],
            "reflection": {
                "state": "insufficient",
                "strategy": "stop",
                "round": 1,
                "max_rounds": 1,
                "stop_reason": "round_limit",
                "reason": "补查仍无材料",
            },
            "unresolved_questions": ["现场何时停止检票？"],
        }
    ]
    result = score_run(dataset, case, CandidateRun.model_validate(snapshot), [1])
    assert result["evidence"]["expert_finding_statuses"] == {"insufficient": 1}
    reflection = result["evidence"]["reflection"]
    assert reflection["stops"] == {"round_limit": 1}
    assert reflection["unresolved_questions"][0]["questions"] == ["现场何时停止检票？"]
    assert "reflection limit differs from depth" in reflection["violations"][0]


def test_reflection_audit_uses_the_same_source_identity_as_runtime(dataset, case, candidate):
    items = [
        Evidence.model_validate(
            candidate.evidence[0].model_dump()
            | {
                "evidence_id": uuid4(),
                "source_url": f"https://archive.example.org/{index}",
                "excerpt": excerpt,
            }
        )
        for index, excerpt in enumerate(("古城开放。", "古城关闭。"))
    ]
    ids = [item.evidence_id for item in items]
    assignment_id = uuid4()
    reviewed_at = dataset.reference(case).as_of
    assessments = []
    for item in items:
        rule = {"score": 2, "reason": "已核验"}
        semantic = rule | {"quote": item.excerpt}
        assessments.append(
            {
                "evidence_id": item.evidence_id,
                "assignment_id": assignment_id,
                "as_of": reviewed_at,
                "max_age_days": None,
                "traceability": rule,
                "authority": rule,
                "freshness": rule,
                "relevance": semantic,
                "directness": semantic,
                "model_id": "test-model",
                "total_score": 10,
                "level": "high",
            }
        )
    export = {
        "assignment_id": assignment_id,
        "research": {
            "run_id": uuid4(),
            "agent_id": "history_culture",
            "kind": "research",
            "evidence": items,
            "findings": [
                {"statement": "开放状态有分歧", "support": "disputed", "evidence_ids": ids}
            ],
        },
        "quality": assessments,
        "reflection": {
            "state": "conflicting",
            "strategy": "stop",
            "round": 0,
            "max_rounds": 0,
            "stop_reason": "budget_exhausted",
            "reason": "来源存在分歧",
            "evidence_ids": ids,
            "model_id": "test-model",
        },
        "unresolved_questions": ["是否开放？"],
    }
    snapshot = CandidateRun.model_validate(candidate.model_dump() | {"expert_research": [export]})
    audit = score_run(dataset, case, snapshot, [1])["evidence"]["reflection"]
    assert any("conflicting evidence shares a source" in note for note in audit["violations"])


def test_main_baseline_scores_real_graph_events_and_detects_missing_final_review(dataset):
    batch = RunBatch.model_validate_json((DATA / "main-runs.json").read_bytes())
    judgments = TypeAdapter(list[Judgment]).validate_json(
        (DATA / "main-judgments.json").read_bytes()
    )
    report = evaluate(dataset, batch, [1, 3, 5], judgments)
    replay = report["summary"]["fixture_replay"]["pending"]
    assert replay["task_success_rate"]["value"] == 2 / 3
    assert replay["metrics"]["faithfulness"] == {"scored": 2, "macro_average": 0.75}
    assert replay["workflow_traces"] == {"observed": 3, "with_violations": 0}
    assert replay["telemetry"]["total_tokens"]["observed"] == 0
    assert len(report["unrun_cases"]) == 33
    assert report["results"][2]["workflow"]["trace"]["tool_failures"] == 3
    assert report["results"][3]["metrics"]["completeness"]["value"] == 0

    candidate = batch.runs[1]
    events = [event for event in candidate.events if event.payload.type != "report.reviewed"]
    changed = candidate.model_copy(update={"events": events})
    case = next(item for item in dataset.cases if item.key == candidate.case_key)
    violations = score_run(dataset, case, changed, [1])["workflow"]["trace"]["violations"]
    assert "event sequence is incomplete or out of order" in violations
    assert "completed task has no approved final draft" in violations


def test_compares_only_matched_case_and_run_origin(dataset, candidate):
    before = evaluate(dataset, RunBatch(target_commit="a" * 40, runs=[candidate]), [1])
    updated = candidate.model_copy(update={"task_id": uuid4(), "ranked_evidence_ids": []})
    demo = candidate.model_copy(update={"task_id": uuid4(), "origin": "demo"})
    after = evaluate(dataset, RunBatch(target_commit="b" * 40, runs=[updated, demo]), [1])
    comparison = compare(before, after)
    [match] = comparison["matched"]
    assert (match["case_key"], match["origin"]) == (candidate.case_key, "recorded")
    assert match["metric_deltas"]["recall@1"] == -1
    assert match["metric_deltas"]["faithfulness"] is None
    assert match["before"]["runs"] == match["after"]["runs"] == 1
    assert comparison["after_only"] == [{"case_key": candidate.case_key, "origin": "demo"}]
    with pytest.raises(ValueError, match="incompatible"):
        compare(before, after | {"dataset_sha256": "other"})


def test_cli_reproduces_the_checked_in_baseline():
    backend = DATA.parent
    command = [
        executable,
        "-X",
        "utf8",
        "-m",
        "travelmind.evaluation",
        "score",
        "--dataset",
        str(DATA / "cases.json"),
        "--runs",
        str(DATA / "main-runs.json"),
        "--judgments",
        str(DATA / "main-judgments.json"),
        "--json",
        "-",
    ]
    first = run(command, cwd=backend, check=True, capture_output=True).stdout
    second = run(command, cwd=backend, check=True, capture_output=True).stdout
    assert first == second
    assert markdown(json.loads(first)) == (
        backend.parent / "docs" / "evaluation-baseline.md"
    ).read_text(encoding="utf-8")
