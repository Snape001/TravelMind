import assert from "node:assert/strict";
import test from "node:test";
import { appendEvent } from "../src/lib/agent-events";
import { fixtureResearch, FixtureTask } from "../src/lib/lifecycle-fixture";
import { makeAnswers, nextSnapshot, taskControls } from "../src/lib/task-lifecycle";
import { ApiError, createTask, readSession, sendCommand, subscribe } from "../src/lib/task-client";
import { historyKey, readTaskHistory, rememberTask } from "../src/lib/task-history";
import type { AgentEvent, TaskRead } from "../src/lib/research";
import { checkFixtureWrite, fixtureCallback, fixtureLogout, fixtureSession } from "../src/lib/fixture-session";

const brief = { destination: "泉州", topics: ["文化"], requirements: "", depth: "deep" } as const;
const now = Date.parse("2026-11-14T09:00:00Z");
const makeFixture = () => new FixtureTask(fixtureResearch({ ...brief, topics: [...brief.topics] }, now));
const read = (fixture: FixtureTask, time: number) => fixture.snapshot(time);

test("only paused tasks may resume and a running pause request is shown as pending", () => {
  const fixture = makeFixture();
  assert.equal(taskControls(read(fixture, now)).pause, true);
  assert.equal(taskControls(read(fixture, now)).resume, false);
  const accepted = fixture.command("pause", "pause-1", null, now + 600);
  assert.equal(accepted.status, 202);
  assert.equal(accepted.task?.status, "running");
  assert.ok(accepted.task?.pause_requested_at);
  assert.equal(taskControls(accepted.task!).pause, false);
  const paused = read(fixture, now + 1500);
  assert.equal(paused.status, "paused");
  assert.equal(paused.pause_requested_at, null);
  assert.equal(taskControls(paused).resume, true);
  assert.equal(fixture.command("pause", "pause-1", null, now + 1500).task?.status, "running");
  assert.equal(fixture.command("resume", "resume-1", null, now + 1600).task?.status, "queued");
});

test("queued pause is synchronous and stale commands leave task state intact", () => {
  const fixture = makeFixture();
  assert.equal(fixture.command("resume", "wrong", null, now).error, "TASK_STATE_CONFLICT");
  const paused = fixture.command("pause", "queued", null, now);
  assert.equal(paused.status, 200);
  assert.equal(paused.task?.status, "paused");
  assert.equal(read(fixture, now + 9000).status, "paused");
});

test("clarification uses IDs from TaskRead, trims all texts and enforces one answer per question", () => {
  const round = read(makeFixture(), now + 4900).pending_clarification!;
  assert.equal(round.questions.length, 2);
  const [first, second] = round.questions;
  assert.throws(() => makeAnswers(round, { [first.question_id]: " " }), /每道问题/);
  assert.throws(() => makeAnswers(round, { [first.question_id]: "😀".repeat(4001), [second.question_id]: "步行" }), /4,000/);
  assert.deepEqual(makeAnswers(round, { [first.question_id]: " 周末 ", [second.question_id]: "步行" }), {
    answers: [{ question_id: first.question_id, text: "周末" }, { question_id: second.question_id, text: "步行" }],
  });
});

test("answer replays the original response and never accepts a stale or mismatched round", () => {
  const fixture = makeFixture();
  const round = read(fixture, now + 4000).pending_clarification!;
  const answers = makeAnswers(round, Object.fromEntries(round.questions.map(({ question_id }) => [question_id, "周末"])));
  assert.equal(fixture.command("answer", "invalid", { roundId: round.clarification_id, body: { answers: answers.answers.slice(1) } }, now + 4000).error, "VALIDATION_ERROR");
  const command = { roundId: round.clarification_id, body: answers };
  const accepted = fixture.command("answer", "answer-1", command, now + 4000);
  assert.equal(accepted.status, 202);
  assert.equal(accepted.task?.status, "queued");
  assert.equal(accepted.task?.pending_clarification, null);
  assert.equal(fixture.command("answer", "answer-2", command, now + 5000).error, "CLARIFICATION_STALE");
  assert.deepEqual(fixture.command("answer", "answer-1", { ...command, body: { answers: [...answers.answers].reverse() } }, now + 5000), accepted);
  assert.equal(fixture.command("answer", "answer-1", { ...command, body: { answers: answers.answers.map((item) => ({ ...item, text: "工作日" })) } }, now + 5000).error, "IDEMPOTENCY_CONFLICT");
  const events = fixture.eventsAt(now + 60000);
  assert.equal(events.at(-1)?.payload.type, "task.status");
  assert.deepEqual(events.map((event) => event.sequence), events.map((_, index) => index + 1));
  assert.ok(events.some((event) => event.payload.type === "task.clarification" && typeof event.payload.questions[0] === "string"));
  assert.equal(read(fixture, now + 60000).status, "completed");
});

test("stale API responses and historical events cannot regress the current snapshot", () => {
  const fixture = makeFixture();
  const queued = read(fixture, now);
  const current = read(fixture, now + 4000);
  assert.equal(nextSnapshot(current, queued), current);
  assert.equal(nextSnapshot(queued, current), current);
  assert.equal(nextSnapshot(current, { ...current, trace_id: crypto.randomUUID() }), current);
  const precise = { ...current, updated_at: "2026-11-14T09:00:02.800200Z" };
  assert.equal(nextSnapshot(precise, { ...queued, updated_at: "2026-11-14T17:00:02.800100+08:00" }), precise);
});

test("recent task links are isolated by account and transport and contain only IDs", () => {
  const values = new Map<string, string>();
  const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } };
  const a = historyKey("account-a", false);
  const b = historyKey("account-b", false);
  const id = crypto.randomUUID();
  rememberTask(storage, a, id);
  const next = crypto.randomUUID();
  rememberTask(storage, a, next);
  rememberTask(storage, a, id);
  assert.deepEqual(readTaskHistory(storage, a), [id, next]);
  assert.deepEqual(readTaskHistory(storage, b), []);
  assert.deepEqual(readTaskHistory(storage, historyKey("account-a", true)), []);
  assert.deepEqual(JSON.parse(values.get(a)!), [id, next]);
});

test("HTTP client sends CSRF and idempotency headers, with a body only for answers", async (t) => {
  const fixture = makeFixture();
  const task = read(fixture, now);
  const calls: [string, RequestInit | undefined][] = [];
  t.mock.method(globalThis, "fetch", async (url: string, options?: RequestInit) => {
    calls.push([url, options]);
    return Response.json(task);
  });
  await createTask({ ...brief, topics: [...brief.topics] }, "create-key", "session-token");
  await sendCommand(task.task_id, { operation: "pause" }, "pause-key", "session-token");
  await sendCommand(task.task_id, { operation: "resume" }, "resume-key", "session-token");
  const round = read(fixture, now + 4000).pending_clarification!;
  const body = makeAnswers(round, Object.fromEntries(round.questions.map(({ question_id }) => [question_id, "步行"])));
  await sendCommand(task.task_id, { operation: "answer", roundId: round.clarification_id, body }, "answer-key", "session-token");
  assert.deepEqual(calls.map(([url]) => url), [
    "/api/v1/tasks", `/api/v1/tasks/${task.task_id}/pause`, `/api/v1/tasks/${task.task_id}/resume`,
    `/api/v1/tasks/${task.task_id}/clarifications/${round.clarification_id}/answer`,
  ]);
  assert.deepEqual(calls.map(([, init]) => new Headers(init?.headers).get("Idempotency-Key")), ["create-key", "pause-key", "resume-key", "answer-key"]);
  assert.ok(calls.every(([, init]) => new Headers(init?.headers).get("X-CSRF-Token") === "session-token"));
  assert.equal(calls[1][1]?.body, undefined);
  assert.equal(calls[2][1]?.body, undefined);
  assert.deepEqual(JSON.parse(String(calls[3][1]?.body)), body);
});

test("session expiry and typed business conflicts remain distinguishable", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json({ detail: { code: "UNAUTHENTICATED", message: "请登录", retryable: false } }, { status: 401 }));
  await assert.rejects(readSession(), (error: unknown) => error instanceof ApiError && error.status === 401 && error.code === "UNAUTHENTICATED");
});

test("fixture session requires same-origin CSRF and is revoked at logout", async () => {
  const callback = fixtureCallback();
  assert.equal(callback.status, 302);
  assert.equal(callback.headers.get("Location"), "/workbench");
  const cookie = callback.headers.get("Set-Cookie")!.split(";")[0];
  const url = "http://localhost:3107/mock/api/v1/auth/me";
  const user = fixtureSession(new Request(url, { headers: { Cookie: cookie } }))!;
  assert.ok(user.user_id);
  const request = (origin: string) => new Request(url, {
    headers: { Cookie: cookie, Host: "127.0.0.1:3107", Origin: origin, "X-CSRF-Token": user.csrf_token },
  });
  assert.equal(checkFixtureWrite(request("http://example.test"), user), false);
  assert.equal(fixtureLogout(request("http://example.test")).status, 403);
  assert.equal(fixtureLogout(request("http://127.0.0.1:3107")).status, 204);
  assert.equal(fixtureSession(request("http://127.0.0.1:3107")), undefined);
});

test("SSE reconnects from the last accepted sequence and deduplicates replay", (t) => {
  const fixture = makeFixture();
  const task = read(fixture, now);
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
  t.mock.method(globalThis, "setTimeout", (callback: () => void) => { queueMicrotask(callback); return 1; });
  const original = globalThis.EventSource;
  globalThis.EventSource = FakeSource as unknown as typeof EventSource;
  const states: string[] = [];
  let events: AgentEvent[] = [];
  const close = subscribe(task.task_id, 0, (event) => {
    const next = appendEvent(events, event, task);
    if (next === events) return false;
    events = next;
    return true;
  }, (state) => states.push(state));
  return Promise.resolve().then(() => {
    const [first, second] = fixture.eventsAt(now + 600);
    sources[0].onopen?.();
    sources[0].send(first);
    sources[0].send({ ...second, trace_id: crypto.randomUUID() });
    sources[0].onerror?.();
    assert.equal(sources[0].stopped, true);
    return Promise.resolve().then(() => {
      assert.match(sources[1].url, /after=1$/);
      sources[1].onopen?.();
      sources[1].send(first);
      sources[1].send(second);
      assert.deepEqual(events, [first, second]);
      assert.deepEqual(states, ["open", "reconnecting", "open"]);
    });
  }).finally(() => { close(); globalThis.EventSource = original; });
});

test("historical task snapshots missing new optional fields still render", () => {
  const legacy: TaskRead = { ...makeFixture().research.task };
  delete legacy.pending_clarification;
  delete legacy.pause_requested_at;
  assert.deepEqual(taskControls(legacy), { pause: true, resume: false, pausing: false, round: null });
});
