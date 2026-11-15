from collections import Counter, defaultdict
from collections.abc import Iterable
from itertools import combinations
from statistics import mean
from uuid import UUID

from travelmind.evaluation.contracts import (
    CandidateRun,
    Case,
    Dataset,
    Judgment,
    RunBatch,
    fingerprint,
)
from travelmind.research.quality_rules import distinct_origins
from travelmind.research.sources import source_key
from travelmind.schemas.domain import Evidence
from travelmind.schemas.research_quality import REFLECTION_ROUND_LIMITS, EvaluationCase

SCORER_VERSION = "v1"


def ratio(numerator: int, denominator: int | None, judged: int | None = None) -> dict:
    if judged is None:
        judged = denominator or 0
    return {
        "numerator": numerator,
        "denominator": denominator,
        "judged": judged,
        "value": numerator / denominator if denominator and judged == denominator else None,
    }


def retrieval_metrics(
    reference: EvaluationCase, run: CandidateRun, evidence: dict[UUID, Evidence], ks: list[int]
) -> tuple[dict, dict]:
    known = {source_key(item) for item in reference.reference_evidence}
    ranked = run.ranked_evidence_ids
    metrics, details = {}, {}
    for k in ks:
        top = None if ranked is None else ranked[:k]
        matches = {}
        for item in top or ():
            key = source_key(evidence[item])
            if key in known:
                matches.setdefault(key, item)
        judged = top is not None
        metrics[f"recall@{k}"] = ratio(len(matches), len(known), len(known) if judged else 0)
        metrics[f"precision@{k}"] = ratio(len(matches), k, k if judged else 0)
        details[str(k)] = {
            "observed_ranks": None if top is None else len(top),
            "matched_candidate_evidence_ids": sorted(map(str, matches.values())),
            "matched_reference_evidence_ids": [
                str(item.evidence_id)
                for item in reference.reference_evidence
                if source_key(item) in matches
            ],
        }
    return metrics, details


def report_metrics(
    reference: EvaluationCase,
    run: CandidateRun,
    evidence: dict[UUID, Evidence],
    judgment: Judgment | None,
) -> tuple[dict, list[dict], list[dict], list[dict]]:
    if judgment is not None:
        if (
            judgment.task_id != run.task_id
            or judgment.case_key != run.case_key
            or judgment.run_sha256 != fingerprint(run)
            or judgment.case_sha256 != fingerprint(reference)
        ):
            raise ValueError(
                f"judgment does not match the snapshot for {run.case_key}/{run.task_id}"
            )
        if run.report is None:
            raise ValueError("cannot attach a semantic judgment without a report snapshot")

    sections = run.report.sections if run.report else []
    citations = {
        (index, item) for index, section in enumerate(sections) for item in section.evidence_ids
    }
    resolved = {pair for pair in citations if pair[1] in evidence}
    claims = judgment.claims if judgment else []
    claim_keys = {(claim.section, claim.quote) for claim in claims}
    if len(claim_keys) != len(claims):
        raise ValueError("report claims must have distinct section/quote pairs")
    valid = set()
    for claim in claims:
        if claim.section >= len(sections) or claim.quote not in sections[claim.section].body:
            raise ValueError("claim quote must occur in the specified report section")
        support = {(claim.section, item) for item in claim.supporting_evidence_ids}
        if support - resolved:
            raise ValueError("claim support must be a resolved citation in the claim section")
        valid.update(support)

    complete = judgment is not None and judgment.inventory_complete
    unknown_sections = {claim.section for claim in claims if claim.verdict == "unavailable"}
    citation_details = []
    for section, evidence_id in sorted(citations):
        pair = section, evidence_id
        if pair in valid:
            verdict = "valid"
        elif pair not in resolved or (complete and section not in unknown_sections):
            verdict = "invalid"
        else:
            verdict = "unavailable"
        citation_details.append(
            {
                "section": section,
                "evidence_id": str(evidence_id),
                "resolved": pair in resolved,
                "verdict": verdict,
            }
        )

    coverage = {item.reference: item for item in judgment.coverage} if judgment else {}
    if judgment and (
        len(coverage) != len(judgment.coverage)
        or set(coverage) - set(range(len(reference.reference_answer)))
    ):
        raise ValueError("coverage must reference distinct existing reference findings")
    coverage_details = []
    for index, finding in enumerate(reference.reference_answer):
        item = coverage.get(index)
        claim = None
        if item and item.claim is not None:
            if item.claim >= len(claims):
                raise ValueError("coverage claim index does not exist")
            claim = claims[item.claim]
        coverage_details.append(
            {
                "reference": index,
                "statement": finding.statement,
                "reference_support": finding.support,
                "reference_evidence_ids": list(map(str, finding.evidence_ids)),
                "verdict": item.verdict if item else "unavailable",
                "reason": item.reason if item else "no judgment for this reference",
                "claim": item.claim if item else None,
                "candidate_evidence_ids": list(map(str, claim.supporting_evidence_ids))
                if claim
                else [],
            }
        )

    claim_counts = Counter(claim.verdict for claim in claims)
    coverage_counts = Counter(item["verdict"] for item in coverage_details)
    metrics = {
        "citation_resolution": ratio(len(resolved), len(citations)),
        "citation_accuracy": ratio(
            len(valid),
            len(citations),
            sum(item["verdict"] != "unavailable" for item in citation_details),
        ),
        "faithfulness": ratio(
            claim_counts["supported"],
            len(claims) if complete else None,
            len(claims) - claim_counts["unavailable"],
        ),
        "completeness": ratio(
            coverage_counts["covered"],
            len(coverage_details),
            len(coverage_details) - coverage_counts["unavailable"],
        ),
    }
    return metrics, coverage_details, citation_details, [c.model_dump(mode="json") for c in claims]


def evidence_metrics(
    reference: EvaluationCase, run: CandidateRun, evidence: dict[UUID, Evidence]
) -> dict:
    experts = run.expert_research or []
    findings = {result.run_id: result.findings for result in run.research or []}
    findings.update({expert.research.run_id: expert.research.findings for expert in experts})
    known_ids = (
        evidence.keys()
        | {item.evidence_id for expert in experts for item in expert.research.evidence}
        | {item.evidence_id for result in run.research or [] for item in result.evidence}
    )
    references = [
        item
        for result in findings.values()
        for finding in result
        for item in set(finding.evidence_ids)
    ]
    reflection = None
    if run.expert_research is not None:
        violations = []
        for expert in experts:
            action = expert.reflection
            if action.max_rounds != REFLECTION_ROUND_LIMITS[reference.brief.depth]:
                violations.append(f"{expert.assignment_id}: reflection limit differs from depth")
            if action.state == "conflicting":
                cited = set(action.evidence_ids) & {
                    card.evidence_id for card in expert.quality if card.level != "low"
                }
                sources = [item for item in expert.research.evidence if item.evidence_id in cited]
                if not any(
                    distinct_origins(left, right) for left, right in combinations(sources, 2)
                ):
                    violations.append(
                        f"{expert.assignment_id}: conflicting evidence shares a source"
                    )
        reflection = {
            "assignments": len(experts),
            "stops": dict(Counter(expert.reflection.stop_reason for expert in experts)),
            "quality_levels": dict(
                Counter(card.level for expert in experts for card in expert.quality)
            ),
            "unresolved_questions": [
                {
                    "assignment_id": str(expert.assignment_id),
                    "questions": expert.unresolved_questions,
                }
                for expert in experts
                if expert.unresolved_questions
            ],
            "violations": violations,
        }
    distinct_sources = {source_key(item) for item in evidence.values()}
    return {
        "snapshot_records": len(evidence),
        "unique_sources": len(distinct_sources),
        "duplicate_source_records": len(evidence) - len(distinct_sources),
        "expert_finding_statuses": (
            dict(Counter(finding.support for result in findings.values() for finding in result))
            if run.research is not None or run.expert_research is not None
            else None
        ),
        "expert_citation_resolution": ratio(
            sum(item in known_ids for item in references), len(references)
        ),
        "unresolved_expert_evidence_ids": sorted(
            {str(item) for item in references if item not in known_ids}
        ),
        "reflection": reflection,
    }


def workflow_metrics(run: CandidateRun) -> dict:
    terminal = run.status in {"completed", "failed"}
    result = {
        "task_success": run.status == "completed" and run.report is not None if terminal else None,
        "trace": None,
    }
    if run.events is None:
        return result
    trace = {
        "events": len(run.events),
        "statuses": [],
        "revision_requests": 0,
        "agent_failures": 0,
        "tool_failures": 0,
        "violations": [],
    }
    violations = trace["violations"]
    started: dict[tuple[str, UUID], str] = {}
    tools: dict[tuple[UUID, UUID], tuple[str, str]] = {}
    ready: dict[UUID, int] = {}
    reviewed: dict[UUID, str] = {}
    identities = {(event.task_id, event.trace_id) for event in run.events}
    if len(identities) != 1 or any(task_id != run.task_id for task_id, _ in identities):
        violations.append("events do not identify a single matching task/trace")
    if [event.sequence for event in run.events] != list(range(1, len(run.events) + 1)):
        violations.append("event sequence is incomplete or out of order")
    if len({event.event_id for event in run.events}) != len(run.events):
        violations.append("duplicate event IDs")
    for event in run.events:
        payload = event.payload
        kind = payload.type
        if kind == "task.status":
            trace["statuses"].append(payload.status)
        elif kind.startswith("agent."):
            key = payload.agent_id, payload.run_id
            if kind == "agent.started":
                if key in started:
                    violations.append(f"{event.sequence}: repeated agent start")
                started[key] = "started"
            elif key not in started:
                violations.append(f"{event.sequence}: {kind} has no matching agent")
            elif kind == "agent.finished":
                if started[key] != "started":
                    violations.append(f"{event.sequence}: repeated agent finish")
                started[key] = payload.status
                trace["agent_failures"] += payload.status == "failed"
        elif kind.startswith("tool."):
            key = payload.run_id, payload.tool_call_id
            if kind == "tool.started":
                if key in tools:
                    violations.append(f"{event.sequence}: repeated tool start")
                tools[key] = payload.agent_id, "started"
            elif tools.get(key) != (payload.agent_id, "started"):
                violations.append(f"{event.sequence}: tool finish has no matching start")
            else:
                tools[key] = payload.agent_id, payload.status
                trace["tool_failures"] += payload.status == "failed"
        elif kind == "report.ready":
            if payload.report_id in ready or payload.version <= max(ready.values(), default=0):
                violations.append(f"{event.sequence}: report version is duplicated or out of order")
            ready[payload.report_id] = payload.version
        elif kind == "report.reviewed":
            if payload.report_id in reviewed:
                violations.append(f"{event.sequence}: repeated review of the same draft")
            reviewed[payload.report_id] = payload.decision
            trace["revision_requests"] += payload.decision == "revision_required"

    if not trace["statuses"] or trace["statuses"][-1] != run.status:
        violations.append("last task status differs from the snapshot")
    if terminal and (
        "started" in started.values() or any(v == "started" for _, v in tools.values())
    ):
        violations.append("terminal trace contains unfinished agents or tools")
    if run.status == "completed":
        latest = max(ready, key=ready.get) if ready else None
        if (
            latest is None
            or reviewed.get(latest) != "approved"
            or latest != next(reversed(reviewed), None)
        ):
            violations.append("completed task has no approved final draft")
        if run.report_id is not None and run.report_id != latest:
            violations.append("report snapshot does not identify the final draft")
    return result | {"trace": trace}


def score_run(
    dataset: Dataset, case: Case, run: CandidateRun, ks: list[int], judgment: Judgment | None = None
) -> dict:
    if case.key != run.case_key:
        raise ValueError("candidate and case keys must match")
    reference = dataset.reference(case)
    evidence = {item.evidence_id: item for item in run.evidence}
    retrieval, ranking = retrieval_metrics(reference, run, evidence, ks)
    report, coverage, citations, claims = report_metrics(reference, run, evidence, judgment)
    return {
        "case_key": case.key,
        "case_id": str(reference.case_id),
        "question": case.question,
        "destination": case.destination,
        "review_status": case.status,
        "task_id": str(run.task_id),
        "origin": run.origin,
        "source": run.source,
        "status": run.status,
        "failure_code": run.failure_code,
        "candidate_sha256": fingerprint(run),
        "reference_sha256": fingerprint(reference),
        "judge": {
            "id": judgment.judge_id,
            "type": judgment.judge_type,
            "sha256": fingerprint(judgment),
            "inventory_complete": judgment.inventory_complete,
        }
        if judgment
        else None,
        "metrics": retrieval | report,
        "ranking": ranking,
        "evidence": evidence_metrics(reference, run, evidence),
        "coverage": coverage,
        "citations": citations,
        "claims": claims,
        "workflow": workflow_metrics(run),
        "usage": run.usage.observations(),
    }


def stats(values: Iterable[int | float | None]) -> dict:
    observed = [value for value in values if value is not None]
    return {
        "observed": len(observed),
        "total": sum(observed) if observed else None,
        "mean": mean(observed) if observed else None,
    }


def summarize(rows: list[dict]) -> dict:
    successes = [row["workflow"]["task_success"] for row in rows]
    traces = [row["workflow"]["trace"] for row in rows if row["workflow"]["trace"] is not None]
    metrics = {}
    for name in rows[0]["metrics"]:
        values = stats(row["metrics"][name]["value"] for row in rows)
        metrics[name] = {"scored": values["observed"], "macro_average": values["mean"]}
    return {
        "runs": len(rows),
        "cases": len({row["case_key"] for row in rows}),
        "by_destination": dict(Counter(row["destination"] for row in rows)),
        "task_status": dict(Counter(row["status"] for row in rows)),
        "task_success_rate": ratio(
            successes.count(True), sum(item is not None for item in successes)
        ),
        "metrics": metrics,
        "telemetry": {name: stats(row["usage"][name] for row in rows) for name in rows[0]["usage"]},
        "workflow_traces": {
            "observed": len(traces),
            "with_violations": sum(bool(t["violations"]) for t in traces),
        },
    }


def compare(before: dict, after: dict) -> dict:
    """Compare evaluated results for the same case and acquisition mode."""
    if any(before[key] != after[key] for key in ("dataset_sha256", "scorer_version", "k")):
        raise ValueError("incompatible dataset, scorer version, or retrieval cutoffs")

    def by_case(report: dict) -> dict:
        groups = defaultdict(list)
        for row in report["results"]:
            groups[row["case_key"], row["origin"]].append(row)
        return groups

    def identity(key: tuple[str, str]) -> dict:
        return {"case_key": key[0], "origin": key[1]}

    old, new = by_case(before), by_case(after)
    matched = []
    for key in sorted(old.keys() & new.keys()):
        previous, current = summarize(old[key]), summarize(new[key])
        deltas = {}
        for name, item in previous["metrics"].items():
            a, b = item["macro_average"], current["metrics"][name]["macro_average"]
            deltas[name] = b - a if a is not None and b is not None else None
        matched.append(
            identity(key) | {"before": previous, "after": current, "metric_deltas": deltas}
        )
    return {
        "before_commit": before["target_commit"],
        "after_commit": after["target_commit"],
        "dataset_sha256": before["dataset_sha256"],
        "scorer_version": before["scorer_version"],
        "k": before["k"],
        "matched": matched,
        "before_only": [identity(key) for key in sorted(old.keys() - new.keys())],
        "after_only": [identity(key) for key in sorted(new.keys() - old.keys())],
    }


def evaluate(
    dataset: Dataset, batch: RunBatch, ks: list[int], judgments: Iterable[Judgment] = ()
) -> dict:
    if not ks or any(type(k) is not int or k <= 0 for k in ks):
        raise ValueError("k must be a positive integer")
    ks = sorted(set(ks))
    cases = {case.key: case for case in dataset.cases}
    by_task = {}
    for judgment in judgments:
        if judgment.task_id in by_task:
            raise ValueError("multiple judgments for one task")
        by_task[judgment.task_id] = judgment
    rows, groups = [], defaultdict(lambda: defaultdict(list))
    for run in batch.runs:
        if run.case_key not in cases:
            raise ValueError(f"unknown case: {run.case_key}")
        case = cases[run.case_key]
        row = score_run(dataset, case, run, ks, by_task.pop(run.task_id, None))
        rows.append(row)
        groups[run.origin][case.status].append(row)
    if by_task:
        raise ValueError("judgments reference tasks outside the run batch")
    return {
        "scorer_version": SCORER_VERSION,
        "target_commit": batch.target_commit,
        "dataset_version": dataset.version,
        "dataset_sha256": fingerprint(dataset),
        "run_batch_sha256": fingerprint(batch),
        "k": ks,
        "inventory": dataset.inventory(),
        "unrun_cases": sorted(cases.keys() - {run.case_key for run in batch.runs}),
        "summary": {
            origin: {status: summarize(items) for status, items in statuses.items()}
            for origin, statuses in groups.items()
        },
        "results": rows,
    }
