import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { buildSubmission, questionnairePages, type Answers, type Questionnaire } from "../src/lib/intake";
import { answerQuestionnaire, createDestinationTask } from "../src/lib/task-client";

const fixtures = JSON.parse(readFileSync("../fixtures/phase4.json", "utf8")) as {
  destination_questionnaire: Questionnaire;
  research_questionnaire: Questionnaire;
  travel_questionnaire: Questionnaire;
};

test("an actual choice is required even when there is only one displayed POI", () => {
  const round = fixtures.destination_questionnaire;
  const question = round.questions[0];
  const onlyOne = { ...round, questions: [{ ...question, options: question.options?.slice(0, 1) }] };
  assert.throws(() => buildSubmission(onlyOne, {}), /请选择/);
  const answers: Answers = { [question.question_id]: [question.options![0].option_id] };
  assert.equal(buildSubmission(onlyOne, answers).answers[0].kind, "single_choice");
});

test("pages follow branching and only submit visible answers before the final notes", () => {
  const round = fixtures.research_questionnaire;
  const followup = round.questions.find((question) => question.visible_if)!;
  const parent = followup.visible_if!;
  const [option] = followup.options ?? [];
  const answers: Answers = {
    [parent.question_id]: [parent.includes_option_id],
    [followup.question_id]: option ? [option.option_id] : " 石刻 ",
    additional_notes: " 参考地方档案 ",
  };
  assert.equal(questionnairePages(round, answers).at(-1)?.questions[0].question_id, "additional_notes");
  assert.ok(buildSubmission(round, answers).answers.some((answer) => answer.question_id === followup.question_id));
  const submission = buildSubmission(round, { ...answers, [parent.question_id]: [] });
  assert.deepEqual(submission.answers.map((answer) => answer.question_id), ["additional_notes"]);
  assert.deepEqual(submission.answers.at(-1), {
    question_id: "additional_notes", kind: "text", text: "参考地方档案",
  });
});

test("travel is optional, preserves zero budget and validates calendar order", () => {
  const round = fixtures.travel_questionnaire;
  assert.equal(questionnairePages(round, {}).length, 3);
  assert.deepEqual(buildSubmission(round, {}).answers, []);
  assert.deepEqual(buildSubmission(round, { budget_cny: "0" }).answers, [
    { kind: "integer", question_id: "budget_cny", value: 0 },
  ]);
  assert.throws(() => buildSubmission(round, { start_date: "2026-12-16", end_date: "2026-12-15" }), /返程日期/);
});

test("intake writes use the frozen paths, CSRF, and idempotency", async (context) => {
  const requests: [string, RequestInit][] = [];
  context.mock.method(globalThis, "fetch", async (path: string, init: RequestInit) => {
    requests.push([path, init]);
    return Response.json({});
  });
  await createDestinationTask({ destination: "开元寺" }, "create-key", "csrf");
  await answerQuestionnaire("task", "round", { answers: [] }, "answer-key", "csrf");
  assert.deepEqual(requests.map(([path, init]) => [
    path, init.method, (init.headers as Headers).get("X-CSRF-Token"),
    (init.headers as Headers).get("Idempotency-Key"), JSON.parse(init.body as string),
  ]), [
    ["/api/v1/tasks/from-destination", "POST", "csrf", "create-key", { destination: "开元寺" }],
    ["/api/v1/tasks/task/questionnaires/round/answer", "POST", "csrf", "answer-key", { answers: [] }],
  ]);
});
