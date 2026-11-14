import assert from "node:assert/strict";
import test from "node:test";
import { eventCursor, readEvent, summarize } from "../src/lib/agent-events";
import { demo, demoResearch } from "../src/lib/demo";
import { prepareBrief } from "../src/lib/research";
import { buildResearch, encodeEvent, eventsAfter, streamResearch, taskAt } from "./fixtures/mock-research";

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
  const cursor = eventCursor(research.task, 0);
  assert.equal(cursor.accept(first), true);
  assert.equal(cursor.accept(first), false);
  assert.equal(cursor.accept({ ...first, sequence: 2 }), false);
  assert.equal(cursor.accept({ ...research.events[1], task_id: demo.taskId }), false);
  assert.equal(cursor.accept({ ...research.events[1], trace_id: demoResearch.task.trace_id }), false);
  assert.equal(cursor.accept(research.events[1]), true);
  assert.equal(cursor.accept(first), false);
  assert.equal(cursor.sequence, 2);
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
  assert.notEqual(research.task.task_id, demo.taskId);
});

test("public example preserves its completed replay, report versions and evidence references", () => {
  const { task, events, reports, evidence } = demoResearch;
  assert.equal(task.task_id, demo.taskId);
  assert.equal(task.status, "completed");
  assert.equal(reports.find(({ report_id }) => report_id === demo.reportId)?.content.title, demo.title);
  assert.equal(summarize(task, events).reportDecision, "approved");
  assert.ok(events.some(({ payload }) => payload.type === "report.reviewed" && payload.decision === "revision_required"));
  assert.ok(events.every((event) => readEvent(JSON.stringify(event), event.payload.type, String(event.sequence)) &&
    event.task_id === task.task_id && event.trace_id === task.trace_id));
  const evidenceIds = new Set(evidence.map(({ evidence_id }) => evidence_id));
  for (const report of reports) {
    assert.equal(report.task_id, task.task_id);
    assert.ok(report.content.sections.flatMap(({ evidence_ids }) => evidence_ids ?? []).every((id) => evidenceIds.has(id)));
  }
  assert.ok(evidence.some(({ source_url }) => source_url === "https://whc.unesco.org/en/list/1561/"));
});
