import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { eventCursor, summarize } from "../src/lib/agent-events";
import { demoResearch } from "../src/lib/demo";
import { buildSubmission, prepareDestination, questionnairePages, visibleQuestions, type Answers, type Questionnaire } from "../src/lib/intake";
import { readResearchEvent, projectResearch, publicSourceUrl, usageTotals, toolAction, mergeActivity, type ResearchEvent } from "../src/lib/research-browser";
import { createDestinationTask, readIntake, readResearchSource, sendCommand, subscribeResearch } from "../src/lib/task-client";
import type { components } from "../src/lib/api.generated";

const fixture = JSON.parse(readFileSync("../fixtures/phase4.json", "utf8")) as {
  destination_questionnaire: Questionnaire;
  research_questionnaire: Questionnaire;
  travel_questionnaire: Questionnaire;
  research_events: ResearchEvent[];
  source: components["schemas"]["ResearchSourceRead"];
};

test("destination entry normalizes input and requires confirmation even for a single POI", () => {
  assert.deepEqual(prepareDestination("  开元寺  "), { destination: "开元寺" });
  assert.throws(() => prepareDestination("馆".repeat(81)));
  const round = fixture.destination_questionnaire;
  const question = round.questions[0];
  const onlyOne = { ...round, questions: [{ ...question, options: question.options?.slice(0, 1) }] };
  assert.throws(() => buildSubmission(onlyOne, {}), /请选择/);
  const answers: Answers = { [question.question_id]: [question.options![0].option_id] };
  assert.deepEqual(buildSubmission(onlyOne, answers).answers, [
    { question_id: question.question_id, kind: "single_choice", option_ids: answers[question.question_id] },
  ]);
  assert.throws(() => buildSubmission(onlyOne, { [question.question_id]: ["unknown-poi"] }), /选项/);
  assert.throws(() => buildSubmission(round, {
    [question.question_id]: question.options!.map(({ option_id }) => option_id),
  }), /选项/);
});

test("pages follow branching and only submit visible answers before the final notes", () => {
  const round = fixture.research_questionnaire;
  const followup = round.questions.find((question) => question.visible_if)!;
  const parent = followup.visible_if!;
  const [option] = followup.options ?? [];
  const answers: Answers = {
    [parent.question_id]: [parent.includes_option_id],
    [followup.question_id]: option ? [option.option_id] : " 石刻 ",
    additional_notes: " 参考地方档案 ",
  };
  assert.equal(questionnairePages(round, answers).at(-1)?.questions[0].question_id, "additional_notes");
  assert.ok(visibleQuestions(round, answers).includes(followup));
  assert.ok(buildSubmission(round, answers).answers.some((answer) => answer.question_id === followup.question_id));
  const hidden = { [followup.question_id]: "hidden" };
  assert.ok(!visibleQuestions(round, hidden).includes(followup));
  assert.deepEqual(buildSubmission(round, hidden), { answers: [] });
  const submission = buildSubmission(round, { ...answers, [parent.question_id]: [] });
  assert.deepEqual(submission.answers.map((answer) => answer.question_id), ["additional_notes"]);
  assert.deepEqual(submission.answers.at(-1), {
    question_id: "additional_notes", kind: "text", text: "参考地方档案",
  });
});

test("travel is optional, preserves zero budget and validates calendar order", () => {
  const round = fixture.travel_questionnaire;
  assert.equal(questionnairePages(round, {}).length, 3);
  assert.deepEqual(buildSubmission(round, {}).answers, []);
  assert.deepEqual(buildSubmission(round, { budget_cny: "0" }).answers, [
    { kind: "integer", question_id: "budget_cny", value: 0 },
  ]);
  assert.deepEqual(buildSubmission(round, { start_date: "2026-12-16" }).answers, [
    { kind: "date", question_id: "start_date", value: "2026-12-16" },
  ]);
  assert.throws(() => buildSubmission(round, { budget_cny: "-1" }));
  assert.throws(() => buildSubmission(round, { start_date: "2026-02-30" }), /有效日期/);
  assert.throws(() => buildSubmission(round, { start_date: "2026-12-16", end_date: "2026-12-15" }), /返程日期/);
});

test("research stream validates frames and has its own task-bound cursor", () => {
  const original = fixture.research_events[0];
  const cursor = eventCursor(original, 0);
  const frame = (event: ResearchEvent, name: string = event.payload.type, id = String(event.sequence)) =>
    readResearchEvent(JSON.stringify(event), name, id);
  assert.equal(frame(original, original.payload.type, "2"), null);
  assert.equal(frame(original, "agent.message"), null);
  assert.equal(cursor.accept(frame(original)!), true);
  assert.equal(cursor.accept(frame(original)!), false);
  assert.equal(cursor.accept({ ...fixture.research_events[1], task_id: demoResearch.task.task_id }), false);
  assert.equal(cursor.accept(frame(fixture.research_events[1])!), true);
  assert.equal(cursor.sequence, 2);
  const usage = fixture.research_events.find((event) => event.payload.type === "agent.usage")!;
  if (usage.payload.type !== "agent.usage") throw new Error("Missing usage fixture");
  assert.equal(frame({ ...usage, payload: { ...usage.payload, usage: { ...usage.payload.usage, input_tokens: -1 } } }), null);
});

test("source lifecycle projects retries, independent scores and citations for the published report", () => {
  const sourceEvents = fixture.research_events.filter(({ payload }) => payload.type !== "agent.usage");
  const [discovered, started, succeeded, scored, cited] = sourceEvents;
  if (scored.payload.type !== "research.source.scored" || cited.payload.type !== "research.source.cited" ||
      started.payload.type !== "research.source.fetch") throw new Error("Incomplete source fixture");
  const published = cited.payload.report_id;
  const source = projectResearch(sourceEvents, published).sources[0];
  assert.equal(source.fetch?.status, "succeeded");
  assert.equal(source.scores[0].total_score, 9);
  assert.equal(source.citation?.report_id, published);
  assert.equal(projectResearch(sourceEvents, demoResearch.task.task_id).sources[0].citation, undefined);
  const retry: ResearchEvent = { ...started, payload: { ...started.payload, status: "started", attempt: 2 } };
  const otherScore: ResearchEvent = { ...scored, payload: {
    ...scored.payload, assignment_id: discovered.event_id, total_score: 5, level: "medium",
  } };
  const retried = projectResearch([discovered, started, succeeded, retry, scored, otherScore], published).sources[0];
  assert.equal(retried.fetch?.attempt, 2);
  assert.equal(retried.fetch?.status, "started");
  assert.deepEqual(retried.scores.map(({ total_score }) => total_score), [9, 5]);
  assert.equal(publicSourceUrl("https://example.org/research"), "https://example.org/research");
  for (const url of ["javascript:alert(1)", "https://p@public.org", "https://127.1/x", "http://192.168.0.1", "http://x.local", "http://[::1]"]) {
    assert.equal(publicSourceUrl(url), undefined);
  }
});

test("usage deduplicates provider calls, preserves zero, and leaves unknown metrics absent", () => {
  const original = fixture.research_events.find(({ payload }) => payload.type === "agent.usage")!;
  if (original.payload.type !== "agent.usage") throw new Error("Missing usage fixture");
  const { usage, agent_id, run_id } = original.payload;
  const partial = { ...original, payload: { ...original.payload, usage: {
    ...usage, call_id: original.event_id, message_event_id: null, input_tokens: null, output_tokens: 0,
  } } };
  const view = projectResearch([original, original, partial], undefined);
  const totals = usageTotals(view.usageFor(agent_id, run_id));
  assert.deepEqual(totals, { input: { count: 620, partial: true }, output: { count: 135, partial: false } });
  assert.equal(view.usageFor(agent_id, run_id, usage.message_event_id!).length, 1);
  assert.equal(view.usageFor(agent_id, run_id, original.event_id).length, 0);
  assert.deepEqual(usageTotals(view.usageFor("reviewer", run_id)), {
    input: { count: null, partial: false }, output: { count: null, partial: false },
  });
  assert.equal(usageTotals([partial.payload.usage]).output.count, 0);
});

test("editorial states are independent even without assignments, and technical tool names have friendly actions", () => {
  const events = demoResearch.events.filter(({ payload }) => payload.type !== "plan.ready" &&
    !(payload.type === "agent.finished" && payload.agent_id === "reviewer")).map((event) => {
    const { payload } = event;
    return "agent_id" in payload && payload.agent_id === "report_writer" ? { ...event, payload: { ...payload, agent_id: "writer" } } : event;
  });
  const view = summarize(demoResearch.task, events);
  assert.equal(view.agents.get("report_writer")?.status, "completed");
  assert.equal(view.agents.get("reviewer")?.status, "running");
  assert.equal(view.published?.report_id, demoResearch.reports.at(-1)?.report_id);
  assert.equal(toolAction("retrieve_evidence"), "检索相关资料");
  assert.equal(toolAction("unknown_tool"), "查阅研究资料");
});

test("merged activity preserves order inside each independent stream", () => {
  const core = demoResearch.events.slice(0, 2);
  const actions = projectResearch(fixture.research_events, undefined).actions;
  const displayed = mergeActivity([core[1], core[0]], actions);
  assert.deepEqual(displayed.slice(0, 2).map(({ event_id }) => event_id), [core[1].event_id, core[0].event_id]);
  assert.deepEqual(displayed.slice(2).map(({ event_id }) => event_id), actions.map(({ event_id }) => event_id));
});

test("Phase 4 HTTP and streaming use same-origin paths, typed answers, and an independent replay cursor", async (t) => {
  const original = fixture.research_events[0];
  const requests: [string, RequestInit][] = [];
  const controller = new AbortController();
  const received: ResearchEvent[] = [];
  let connects = 0;
  t.mock.method(globalThis, "fetch", async (url: string, options: RequestInit) => {
    requests.push([url, options]);
    if (url.includes("/research-sources/")) return Response.json(fixture.source);
    if (url.includes("/research-events?")) {
      const events = ++connects === 1 ? [original] : [original, fixture.research_events[1]];
      const bytes = new TextEncoder().encode(events.map((event) =>
        `id: ${event.sequence}\r\nevent: ${event.payload.type}\r\ndata: ${JSON.stringify(event)}\r\n\r\n`).join(""));
      return new Response(new ReadableStream({ start(stream) {
        stream.enqueue(bytes.slice(0, bytes.length - 11));
        stream.enqueue(bytes.slice(bytes.length - 11));
        stream.close();
      } }), { headers: { "Content-Type": "text/event-stream" } });
    }
    return Response.json({ ...demoResearch.task, status: "completed" });
  });
  await createDestinationTask({ destination: "开元寺" }, "key", "csrf", controller.signal);
  await readIntake(original.task_id, controller.signal);
  await sendCommand(original.task_id, { operation: "answer-questionnaire", roundId: "round", body: { answers: [] } }, "key", "csrf");
  assert.equal((await readResearchSource(original.task_id, fixture.source.source_id, controller.signal)).body_text, fixture.source.body_text);
  await new Promise<void>((resolve, reject) => {
    const close = subscribeResearch({ ...demoResearch.task, ...original, status: "running" }, (event) => received.push(event),
      (connection) => {
        if (connection === "closed") { close(); resolve(); }
        if (connection === "unavailable") { close(); reject(new Error("Unexpected stream failure")); }
      }, reject);
  });
  assert.deepEqual(received.map(({ sequence }) => sequence), [1, 2]);
  const base = `/api/v1/tasks/${original.task_id}`;
  assert.deepEqual(requests.slice(0, 4).map(([url]) => url), [
    "/api/v1/tasks/from-destination", `${base}/intake`, `${base}/questionnaires/round/answer`,
    `${base}/research-sources/${fixture.source.source_id}`,
  ]);
  assert.deepEqual(requests.filter(([, options]) => options.method === "POST").map(([, options]) => [
    new Headers(options.headers).get("X-CSRF-Token"), new Headers(options.headers).get("Idempotency-Key"),
    JSON.parse(String(options.body)),
  ]), [["csrf", "key", { destination: "开元寺" }], ["csrf", "key", { answers: [] }]]);
  assert.deepEqual(requests.map(([url]) => url).filter((url) => url.includes("/research-events")),
    [0, 1].map((cursor) => `${base}/research-events?after=${cursor}`));
});
