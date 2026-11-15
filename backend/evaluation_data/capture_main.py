"""Capture the pinned main with local providers, plus the existing frontend demo."""

import argparse
import asyncio
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from travelmind.evaluation.__main__ import json_text
from travelmind.evaluation.archive import TaskArchive
from travelmind.evaluation.contracts import CandidateRun, Dataset, RunBatch, stable_id
from travelmind.runtime.workflow import build_research_graph
from travelmind.schemas.agents import AgentInput, ResearchResult, RunBudget
from travelmind.schemas.domain import Evidence
from travelmind.schemas.events import AgentEvent

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(__file__).resolve().parent
DEMO = "frontend/src/lib/demo-data.ts"
TARGET = "aedb2b7e2598e2a4e444600e2f7d62f6141d3013"


def fixture_evidence(key: str, title: str, url: str, excerpt: str) -> Evidence:
    return Evidence(
        evidence_id=stable_id("fixture", key),
        source_type="official",
        source_title=title,
        source_url=url,
        retrieved_at="2026-11-15T00:00:00+08:00",
        excerpt=excerpt,
    )


@dataclass(frozen=True)
class Replay:
    case_key: str
    sources: tuple[Evidence, ...]
    finding: str
    draft: str
    revision: str | None = None
    retrieval_timeout: bool = False


REPLAYS = (
    Replay(
        "dunhuang-heritage",
        (
            fixture_evidence(
                "geopark",
                "敦煌世界地质公园",
                "https://www.unesco.org/en/iggp/dunhuang-unesco-global-geopark",
                "敦煌地质公园展示雅丹和鸣沙山地貌。",
            ),
            fixture_evidence(
                "mogao",
                "莫高窟世界遗产档案",
                "https://whc.unesco.org/en/list/440/",
                "莫高窟于1987年列入世界遗产名录。",
            ),
        ),
        "莫高窟于1987年列入世界遗产名录。",
        "莫高窟于1987年列入世界遗产名录。莫高窟洞窟全年开放至午夜。",
    ),
    Replay(
        "quanzhou-heritage",
        (
            fixture_evidence(
                "quanzhou",
                "泉州世界遗产档案",
                "https://whc.unesco.org/en/list/1561/",
                "泉州的宋元海洋商贸系列遗产于2021年列名，由22处遗产点组成。",
            ),
        ),
        "泉州的宋元海洋商贸系列遗产于2021年列名，由22处遗产点组成。",
        "泉州的宋元海洋商贸系列遗产由22处遗产点组成。",
        "泉州的宋元海洋商贸系列遗产于2021年列入世界遗产名录。该系列由22处遗产点组成。",
    ),
    Replay("dunhuang-photo", (), "", "", retrieval_timeout=True),
)


async def capture(dataset: Dataset, replay: Replay) -> CandidateRun:
    case = next(case for case in dataset.cases if case.key == replay.case_key)
    reference = dataset.reference(case)
    brief = reference.brief.model_copy(
        update={
            "requirements": (
                f"{reference.brief.requirements}\n参考时点 {reference.as_of.isoformat()}"
            ),
        }
    )
    task_id, trace_id = uuid4(), uuid4()
    source_id = replay.sources[-1].evidence_id if replay.sources else None
    model_calls = 0

    async def model(request: AgentInput, instructions, response_type, /, *, context=None):
        nonlocal model_calls
        model_calls += 1
        match request.agent_id:
            case "research_director":
                response = {"brief": request.brief, "questions": []}
            case "planner":
                response = {
                    "objective": request.brief.topics[0],
                    "assignments": [
                        {"agent_id": agent, "objective": f"核对{request.brief.topics[0]}"}
                        for agent in ("history_culture", "tourism_resources", "travel_planning")
                    ],
                }
            case "writer":
                response = {
                    "title": f"{request.brief.destination}调研",
                    "sections": [
                        {
                            "heading": "遗产事实",
                            "body": replay.revision if request.draft else replay.draft,
                            "author_agent_ids": ["history_culture"],
                            "evidence_ids": [source_id],
                        }
                    ],
                }
            case "reviewer":
                if replay.revision and request.draft.sections[0].body == replay.draft:
                    response = {
                        "decision": "revision_required",
                        "issues": [{"category": "coverage", "message": "补充列入名录的年份。"}],
                    }
                else:
                    response = {"decision": "approved"}
            case _:
                if "交叉质询" in instructions:
                    response = {"finding_index": 0, "question": "这一判断采用什么资料口径？"}
                else:
                    response = {
                        "findings": [
                            {
                                "statement": replay.finding,
                                "evidence_ids": [source_id],
                                "support": "supported",
                            }
                        ]
                    }
        return response_type.model_validate(response)

    async def retrieve(_):
        if replay.retrieval_timeout:
            raise TimeoutError
        return list(replay.sources)

    graph = build_research_graph(model, retrieve=retrieve, budget=RunBudget(max_attempts=1))
    events = []
    state = {}
    started = perf_counter()
    async for mode, payload in graph.astream(
        {"task_id": task_id, "trace_id": trace_id, "brief": brief},
        stream_mode=["values", "custom"],
    ):
        if mode == "values":
            state = payload
        else:
            events.append(
                AgentEvent(
                    event_id=uuid4(),
                    task_id=task_id,
                    trace_id=trace_id,
                    sequence=len(events) + 1,
                    occurred_at=datetime.now(UTC),
                    payload=payload,
                )
            )
    duration_ms = round((perf_counter() - started) * 1000)
    return CandidateRun(
        case_key=replay.case_key,
        task_id=task_id,
        origin="fixture_replay",
        source="main LangGraph 固定模型与检索响应，按工具返回顺序保存排名",
        status=state["status"],
        failure_code=state["failure"].code if "failure" in state else None,
        evidence=list(state.get("evidence", {}).values()),
        ranked_evidence_ids=None
        if replay.retrieval_timeout
        else [item.evidence_id for item in replay.sources],
        research=[
            result
            for result in state.get("results", {}).values()
            if isinstance(result, ResearchResult)
        ],
        report=state.get("draft"),
        report_id=state.get("report_id"),
        events=events,
        usage={"model_calls": model_calls, "duration_ms": duration_ms},
    )


def load_demo() -> CandidateRun:
    source = (ROOT / DEMO).read_text(encoding="utf-8")
    body = source.split("export const demoResearch: DemoResearch = ", 1)[1]
    record = json.loads(body.strip().removesuffix(";"))
    return TaskArchive.model_validate(record).candidate(
        "quanzhou-heritage", f"仓库只读示例 {DEMO}，把遗产问题作为事后覆盖检查", origin="demo"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    status = subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            TARGET,
            "--",
            "backend/travelmind/runtime",
            "backend/travelmind/research",
            "backend/travelmind/schemas",
            "backend/uv.lock",
            DEMO,
        ],
        cwd=ROOT,
        check=False,
    )
    if status.returncode:
        parser.error(f"the candidate code and demo must match main {TARGET}")
    dataset = Dataset.model_validate_json((DATA / "cases.json").read_bytes())

    async def capture_all():
        return [await capture(dataset, replay) for replay in REPLAYS]

    batch = RunBatch(target_commit=TARGET, runs=[*asyncio.run(capture_all()), load_demo()])
    args.output.write_text(
        json_text(batch.model_dump(mode="json", exclude_none=True)), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
