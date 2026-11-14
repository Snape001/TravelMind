import assert from "node:assert/strict";
import test from "node:test";
import { appendEvent, encodeEvent, readEvent, summarize } from "../src/lib/agent-events";
import { prepareBrief } from "../src/lib/research";
import { buildResearch, demoTask, eventsAfter, streamResearch, taskAt } from "../src/lib/mock-research";

const brief = { destination: "泉州", topics: ["古城文化", "公共交通"], requirements: "周末", depth: "deep" } as const;
const now = Date.parse("2026-11-14T03:30:00Z");
const research = buildResearch({ ...brief, topics: [...brief.topics] }, now);
const [first] = research.events;

test("task form normalizes ResearchBrief and deduplicates requested topics", () => {
  assert.deepEqual(prepareBrief(" 泉州 ", " 古城文化，公共交通\n古城文化 ", " 周末 ", "deep"), brief);
});

test("task form applies the API destination, topic and requirement limits", () => {
  assert.throws(() => prepareBrief(" ", "", "", "quick"), /目的地/);
  assert.throws(() => prepareBrief("泉".repeat(201), "", "", "quick"), /目的地/);
  assert.throws(() => prepareBrief("泉州", Array.from({ length: 21 }, (_, i) => `主题${i}`).join("，"), "", "quick"), /20/);
  assert.throws(() => prepareBrief("泉州", "主题".repeat(101), "", "quick"), /主题/);
  assert.throws(() => prepareBrief("泉州", "", "周末".repeat(2001), "quick"), /4,000/);
});

test("mock SSE serializes contract sequence, named event and JSON data", () => {
  assert.equal(encodeEvent(first), `id: 1\nevent: task.status\ndata: ${JSON.stringify(first)}\n\n`);
  assert.deepEqual(readEvent(JSON.stringify(first), "task.status", "1"), first);
});

test("SSE ignores malformed data, unknown versions and mismatched event name or cursor", () => {
  assert.equal(readEvent("null", "task.status", "1"), null);
  assert.equal(readEvent("{", "task.status", "1"), null);
  assert.equal(readEvent(JSON.stringify({ ...first, version: 2 }), "task.status", "1"), null);
  assert.equal(readEvent(JSON.stringify(first), "agent.message", "1"), null);
  assert.equal(readEvent(JSON.stringify(first), "task.status", "2"), null);
});

test("SSE replay scopes events to their task and trace and ignores duplicate or stale frames", () => {
  const accepted = appendEvent([], first, research.task);
  assert.equal(accepted.length, 1);
  assert.equal(appendEvent(accepted, first, research.task), accepted);
  assert.equal(appendEvent(accepted, { ...first, sequence: 2 }, research.task), accepted);
  assert.equal(appendEvent(accepted, { ...first, task_id: demoTask.task.task_id, sequence: 2 }, research.task), accepted);
  assert.equal(appendEvent(accepted, { ...first, trace_id: demoTask.task.trace_id, sequence: 2 }, research.task), accepted);
  assert.equal(appendEvent([research.events[1]], first, research.task).length, 1);
});

test("mock replay delivers only events above the exclusive cursor and at the current time", () => {
  assert.deepEqual(eventsAfter(research, 0, now), [first]);
  const end = Date.parse(research.events.at(-1)!.occurred_at);
  assert.deepEqual(eventsAfter(research, 3, end), research.events.slice(3));
  assert.deepEqual(eventsAfter(research, 3, now), []);
  assert.equal(taskAt(research, now).status, "queued");
  assert.equal(taskAt(research, end).status, "completed");
});

test("SSE stream frames resume after the cursor and terminate on cancellation", async () => {
  const controller = new AbortController();
  const archived = buildResearch({ ...brief, topics: [...brief.topics] }, 0);
  const last = archived.events.at(-1)!;
  const reader = streamResearch(archived, last.sequence - 1, controller.signal).getReader();
  try {
    const frame = await reader.read();
    assert.equal(new TextDecoder().decode(frame.value), encodeEvent(last));
  } finally {
    controller.abort();
  }
  assert.equal((await reader.read()).done, true);
});

test("workbench builds a dynamic plan, run states and tool/citation timeline from AgentEvents", () => {
  const queued = summarize(research.task, [first]);
  assert.equal(queued.plan, null);
  assert.equal(queued.directorStatus, "waiting");
  assert.equal(queued.status, "queued");
  const final = summarize(research.task, research.events);
  assert.equal(final.status, "completed");
  assert.equal(final.directorStatus, "completed");
  assert.equal(final.assignments.filter((assignment) => assignment.status === "completed").length, 5);
  assert.equal(final.report?.report_id, research.reports[1].report_id);
  assert.equal(final.reportDecision, "approved");
  assert.ok(research.events.some(({ payload }) => payload.type === "tool.finished"));
  assert.ok(research.events.some(({ payload }) => payload.type === "agent.message"));
  assert.ok(research.events.some(({ payload }) => payload.type === "report.reviewed" && payload.decision === "revision_required"));
});

test("assignment follows the latest run, task errors and clarification survive replay", () => {
  const started = research.events.find(({ payload }) => payload.type === "agent.started" && payload.assignment_id)!;
  if (started.payload.type !== "agent.started") throw new Error("Missing start");
  const error = { code: "DEMO_ERROR", message: "模拟错误", retryable: true };
  const plan = research.events.find(({ payload }) => payload.type === "plan.ready")!;
  const failed = summarize(research.task, [
    plan, started,
    { ...started, payload: { type: "agent.finished", agent_id: started.payload.agent_id, run_id: started.payload.run_id, status: "failed", error } },
    { ...started, payload: { type: "task.status", status: "failed", error } },
  ]);
  assert.equal(failed.assignments[0].status, "failed");
  assert.deepEqual(failed.error, error);
  const clarification = summarize(research.task, [
    { ...first, payload: { type: "task.status", status: "needs_clarification" } },
    { ...first, payload: { type: "task.clarification", questions: ["计划在哪个季节出发？"] } },
  ]);
  assert.deepEqual(clarification.questions, ["计划在哪个季节出发？"]);
});

test("mock report versions and citations stay scoped to their owning task", () => {
  const evidence = new Set(research.evidence.map((source) => source.evidence_id));
  for (const report of research.reports) {
    assert.equal(report.task_id, research.task.task_id);
    for (const section of report.content.sections) {
      for (const id of section.evidence_ids ?? []) assert.ok(evidence.has(id));
    }
  }
  assert.ok(research.evidence.every((source) => source.task_id === research.task.task_id));
  assert.notEqual(research.task.task_id, demoTask.task.task_id);
});
