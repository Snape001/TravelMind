import assert from "node:assert/strict";
import test from "node:test";
import { makeAnswers, nextSnapshot, taskControls } from "../src/lib/task-lifecycle";
import { ApiError, createDestinationTask, readSession, sendCommand, subscribe } from "../src/lib/task-client";
import { rememberLoginReturn, takeLoginReturn } from "../src/lib/login-return";
import { readTaskHistory, rememberTask } from "../src/lib/task-history";
import { demoResearch } from "../src/lib/demo";
import type { AgentEvent, PendingClarification, TaskRead } from "../src/lib/research";

const queued: TaskRead = {
  ...demoResearch.task, status: "queued", updated_at: "2026-11-14T09:00:00Z",
};
const round: PendingClarification = {
  clarification_id: crypto.randomUUID(),
  questions: ["计划出行几天？", "偏好哪种交通方式？"].map((text) => ({
    question_id: crypto.randomUUID(), text,
  })),
};

test("task controls distinguish legacy snapshots, requested pauses, and pending clarification", () => {
  const legacy = { ...queued };
  delete legacy.pending_clarification;
  delete legacy.pause_requested_at;
  assert.deepEqual(taskControls(legacy), { pause: true, resume: false, pausing: false, round: null });
  assert.deepEqual(taskControls({ ...queued, status: "running", pause_requested_at: queued.updated_at }),
    { pause: false, resume: false, pausing: true, round: null });
  assert.deepEqual(taskControls({ ...queued, status: "paused", pause_requested_at: null }),
    { pause: false, resume: true, pausing: false, round: null });
  assert.deepEqual(taskControls({ ...queued, status: "needs_clarification", pending_clarification: round }),
    { pause: false, resume: false, pausing: false, round });
});

test("clarification uses IDs from TaskRead, trims all texts and enforces one answer per question", () => {
  assert.equal(round.questions.length, 2);
  const [first, second] = round.questions;
  assert.throws(() => makeAnswers(round, { [first.question_id]: " " }), /每道问题/);
  assert.throws(() => makeAnswers(round, { [first.question_id]: "😀".repeat(4001), [second.question_id]: "步行" }), /4,000/);
  assert.deepEqual(makeAnswers(round, { [first.question_id]: " 周末 ", [second.question_id]: "步行" }), {
    answers: [{ question_id: first.question_id, text: "周末" }, { question_id: second.question_id, text: "步行" }],
  });
});

test("stale API responses and historical events cannot regress the current snapshot", () => {
  const current: TaskRead = { ...queued, status: "needs_clarification",
    pending_clarification: round, updated_at: "2026-11-14T09:00:04Z" };
  assert.equal(nextSnapshot(current, queued), current);
  assert.equal(nextSnapshot(queued, current), current);
  assert.equal(nextSnapshot(current, { ...current, trace_id: crypto.randomUUID() }), current);
  const precise = { ...current, updated_at: "2026-11-14T09:00:02.800200Z" };
  assert.equal(nextSnapshot(precise, { ...queued, updated_at: "2026-11-14T17:00:02.800100+08:00" }), precise);
});

test("recent task links are isolated by account, ignore the former fixture namespace and contain only IDs", () => {
  const values = new Map<string, string>();
  const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } };
  const a = "travelmind.live.task-history.v1.account-a";
  const id = crypto.randomUUID();
  const oldFixtureId = crypto.randomUUID();
  storage.setItem("travelmind.fixture.task-history.v1.account-a", JSON.stringify([oldFixtureId]));
  rememberTask("account-a", id, storage);
  const next = crypto.randomUUID();
  rememberTask("account-a", next, storage);
  rememberTask("account-a", id, storage);
  assert.deepEqual(readTaskHistory("account-a", storage), [id, next]);
  assert.deepEqual(readTaskHistory("account-b", storage), []);
  assert.ok(!readTaskHistory("account-a", storage).includes(oldFixtureId));
  assert.deepEqual(JSON.parse(values.get(a)!), [id, next]);
});

test("destination creation and task commands send CSRF, idempotency keys and the expected bodies", async (t) => {
  const task = queued;
  const calls: [string, RequestInit | undefined][] = [];
  t.mock.method(globalThis, "fetch", async (url: string, options?: RequestInit) => {
    calls.push([url, options]);
    return Response.json(task);
  });
  const destination = { destination: task.brief.destination };
  await createDestinationTask(destination, "create-key", "session-token");
  await sendCommand(task.task_id, { operation: "pause" }, "pause-key", "session-token");
  await sendCommand(task.task_id, { operation: "resume" }, "resume-key", "session-token");
  const body = makeAnswers(round, Object.fromEntries(round.questions.map(({ question_id }) => [question_id, "步行"])));
  await sendCommand(task.task_id, { operation: "answer", roundId: round.clarification_id, body }, "answer-key", "session-token");
  assert.deepEqual(calls.map(([url]) => url), [
    "/api/v1/tasks/from-destination", `/api/v1/tasks/${task.task_id}/pause`, `/api/v1/tasks/${task.task_id}/resume`,
    `/api/v1/tasks/${task.task_id}/clarifications/${round.clarification_id}/answer`,
  ]);
  assert.deepEqual(calls.map(([, init]) => new Headers(init?.headers).get("Idempotency-Key")), ["create-key", "pause-key", "resume-key", "answer-key"]);
  assert.ok(calls.every(([, init]) => new Headers(init?.headers).get("X-CSRF-Token") === "session-token"));
  assert.deepEqual(JSON.parse(String(calls[0][1]?.body)), destination);
  assert.equal(calls[1][1]?.body, undefined);
  assert.equal(calls[2][1]?.body, undefined);
  assert.deepEqual(JSON.parse(String(calls[3][1]?.body)), body);
});

test("the HTTP client exposes session expiry as a typed 401", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json({ detail: { code: "UNAUTHENTICATED", message: "请登录", retryable: false } }, { status: 401 }));
  await assert.rejects(readSession(), (error: unknown) => error instanceof ApiError && error.status === 401 && error.code === "UNAUTHENTICATED");
});

test("OIDC return restores the original in-app path once and rejects expired or external destinations", () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
    removeItem: (key: string) => { values.delete(key); },
  };
  const origin = "https://travelmind.localhost";
  const current = new URL(`${origin}/reports?view=recent#evidence-1`);
  rememberLoginReturn(current, storage, 1000);
  assert.equal(takeLoginReturn(origin, storage, 2000), "/reports?view=recent#evidence-1");
  assert.equal(takeLoginReturn(origin, storage, 2000), null);
  rememberLoginReturn(current, storage, 1000);
  assert.equal(takeLoginReturn(origin, storage, 601000), null);

  for (const path of ["//example.com", "/\\example.com", "https://example.com", "/api/v1/auth/login"]) {
    rememberLoginReturn(current, storage, 1000);
    const [key, value] = [...values.entries()][0];
    values.set(key, JSON.stringify({ ...JSON.parse(value), path }));
    assert.equal(takeLoginReturn(origin, storage, 2000), null);
    assert.equal(values.size, 0);
  }
});

test("SSE reconnects from the last accepted sequence and cancels stale streams and reconnects", (t) => {
  const task = queued;
  const sources: FakeSource[] = [];
  class FakeSource extends EventTarget {
    onopen: (() => void) | null = null;
    onerror: (() => void) | null = null;
    stopped = false;
    constructor(readonly url: string) { super(); sources.push(this); }
    close() { this.stopped = true; }
    send(event: AgentEvent) {
      this.dispatchEvent(new MessageEvent(event.payload.type, { data: JSON.stringify(event), lastEventId: String(event.sequence) }));
    }
  }
  const timers = new Map<number, () => void>();
  let timerId = 0;
  t.mock.method(globalThis, "setTimeout", (callback: () => void) => { timers.set(++timerId, callback); return timerId; });
  t.mock.method(globalThis, "clearTimeout", (id: number) => timers.delete(id));
  const original = Object.getOwnPropertyDescriptor(globalThis, "EventSource");
  Object.defineProperty(globalThis, "EventSource", { value: FakeSource, configurable: true });
  const states: string[] = [];
  const events: AgentEvent[] = [];
  const close = subscribe(task, 0, (event) => events.push(event), (state) => states.push(state));
  try {
    const [first, second] = demoResearch.events;
    sources[0].onopen?.();
    sources[0].send(first);
    sources[0].send({ ...second, trace_id: crypto.randomUUID() });
    sources[0].onerror?.();
    assert.equal(sources[0].stopped, true);
    sources[0].send(second);
    assert.deepEqual(events, [first]);
    const reconnect = timers.get(1)!;
    timers.delete(1);
    reconnect();
    assert.match(sources[1].url, /after=1$/);
    sources[1].onopen?.();
    sources[1].send(first);
    sources[1].send(second);
    assert.deepEqual(events, [first, second]);
    assert.deepEqual(states, ["open", "reconnecting", "open"]);
    sources[1].onerror?.();
    assert.equal(timers.size, 1);
    assert.equal(sources[1].stopped, true);
    close();
    assert.equal(timers.size, 0);
  } finally {
    close();
    if (original) Object.defineProperty(globalThis, "EventSource", original);
    else Reflect.deleteProperty(globalThis, "EventSource");
  }
});
