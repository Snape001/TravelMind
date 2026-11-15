import assert from "node:assert/strict";
import test from "node:test";
import { eventCursor, readEvent, summarize } from "../src/lib/agent-events";
import { demo, demoResearch } from "../src/lib/demo";
const { task, events } = demoResearch;
const [first, second] = events;

test("AgentEvent v1 validates frames and accepts only new events for the same task and trace", () => {
  assert.deepEqual(readEvent(JSON.stringify(first), first.payload.type, "1"), first);
  assert.equal(readEvent("null", "task.status", "1"), null);
  assert.equal(readEvent("{", "task.status", "1"), null);
  assert.equal(readEvent(JSON.stringify({ ...first, version: 2 }), "task.status", "1"), null);
  assert.equal(readEvent(JSON.stringify(first), "agent.message", "1"), null);
  assert.equal(readEvent(JSON.stringify(first), "task.status", "2"), null);

  const cursor = eventCursor(task, 0);
  assert.equal(cursor.accept(first), true);
  assert.equal(cursor.accept(first), false);
  assert.equal(cursor.accept({ ...first, sequence: 2 }), false);
  assert.equal(cursor.accept({ ...second, task_id: crypto.randomUUID() }), false);
  assert.equal(cursor.accept({ ...second, trace_id: crypto.randomUUID() }), false);
  assert.equal(cursor.accept(second), true);
  assert.equal(cursor.accept(first), false);
  assert.equal(cursor.sequence, 2);
});

test("the workbench projects queued, failed, and clarification states from AgentEvent v1", () => {
  const queued = summarize(task, [first]);
  assert.equal(queued.status, "queued");
  assert.equal(queued.plan, null);
  assert.equal(queued.directorStatus, "waiting");
  const started = events.find(({ payload }) => payload.type === "agent.started" && payload.assignment_id)!;
  if (started.payload.type !== "agent.started") throw new Error("Missing start");
  const error = { code: "DEMO_ERROR", message: "模拟错误", retryable: true };
  const plan = events.find(({ payload }) => payload.type === "plan.ready")!;
  const failed = summarize(task, [
    plan, started,
    { ...started, payload: { type: "agent.finished", agent_id: started.payload.agent_id, run_id: started.payload.run_id, status: "failed", error } },
    { ...started, payload: { type: "task.status", status: "failed", error } },
  ]);
  assert.equal(failed.assignments[0].status, "failed");
  assert.deepEqual(failed.error, error);
  const clarification = summarize(task, [
    { ...first, payload: { type: "task.status", status: "needs_clarification" } },
    { ...first, payload: { type: "task.clarification", questions: ["计划在哪个季节出发？"] } },
  ]);
  assert.deepEqual(clarification.questions, ["计划在哪个季节出发？"]);
});

test("public example preserves its completed replay, report versions and evidence references", () => {
  const { task, events, reports, evidence } = demoResearch;
  assert.equal(task.task_id, demo.taskId);
  assert.equal(task.status, "completed");
  assert.equal(reports.find(({ report_id }) => report_id === demo.reportId)?.content.title, demo.title);
  const view = summarize(task, events);
  assert.equal(view.status, "completed");
  assert.equal(view.directorStatus, "completed");
  assert.equal(view.assignments.filter(({ status }) => status === "completed").length, 5);
  assert.equal(view.published?.report_id, demo.reportId);
  assert.equal(view.reportDecision, "approved");
  assert.ok(view.toolNames.size > 0);
  assert.ok(events.some(({ payload }) => payload.type === "agent.message"));
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
