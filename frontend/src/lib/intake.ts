import type { components } from "./api.generated";

export type Questionnaire = components["schemas"]["ResearchQuestionnaire"];
export type Intake = components["schemas"]["ResearchIntakeRead"];
export type Question = components["schemas"]["QuestionnaireQuestion"];
export type Destination = components["schemas"]["Destination"];
export type DestinationTaskCreate = components["schemas"]["DestinationTaskCreate"];
export type Submission = components["schemas"]["QuestionnaireSubmission"];
export type Answers = Record<string, string | string[]>;
export const destinationLoginDraft = "travelmind.destination-before-login";

export function prepareDestination(value: string): DestinationTaskCreate {
  const destination = value.trim();
  if (!destination || Array.from(destination).length > 80) throw new Error("请输入 1–80 字的目的地名称。");
  return { destination };
}

export function visibleQuestions(round: Questionnaire, answers: Answers): Question[] {
  const visible = new Set<string>();
  return round.questions.filter((question) => {
    const condition = question.visible_if;
    if (condition) {
      const selected = answers[condition.question_id];
      if (!visible.has(condition.question_id) || !Array.isArray(selected)
        || !selected.includes(condition.includes_option_id)) return false;
    }
    visible.add(question.question_id);
    return true;
  });
}

export function visibleAnswers(round: Questionnaire, answers: Answers): Answers {
  return Object.fromEntries(visibleQuestions(round, answers)
    .filter(({ question_id }) => Object.hasOwn(answers, question_id))
    .map(({ question_id }) => [question_id, answers[question_id]]));
}

const travelPages = [
  { title: "出行时间", fields: ["start_date", "end_date", "stay_days"] },
  { title: "交通与节奏", fields: ["transport_modes", "styles", "intensity"] },
  { title: "预算与同行", fields: ["budget_cny", "party_size", "companions"] },
];

export function questionnairePages(round: Questionnaire, answers: Answers) {
  const questions = visibleQuestions(round, answers);
  if (round.stage !== "travel") return questions.map((question) => ({
    title: question.text, questions: [question],
  }));

  const known = new Set(travelPages.flatMap(({ fields }) => fields));
  return [
    ...travelPages.map(({ title, fields }) => ({
      title, questions: questions.filter(({ question_id }) => fields.includes(question_id)),
    })).filter((page) => page.questions.length > 0),
    ...questions.filter(({ question_id }) => !known.has(question_id))
      .map((question) => ({ title: question.text, questions: [question] })),
  ];
}

export function checkTravelDates(answers: Answers) {
  const { start_date, end_date } = answers;
  if (typeof start_date === "string" && typeof end_date === "string" && end_date && end_date < start_date) {
    throw new Error("返程日期不能早于出发日期。");
  }
}

export function minimum(question: Question) {
  return ["stay_days", "party_size"].includes(question.question_id) ? 1 : 0;
}

export function buildSubmission(round: Questionnaire, values: Answers): Submission {
  const answers: Submission["answers"] = [];
  if (round.stage === "travel") checkTravelDates(values);
  for (const question of visibleQuestions(round, values)) {
    const { question_id, kind } = question;
    const raw = values[question_id];
    if (kind === "single_choice" || kind === "multi_choice") {
      const selected = new Set(Array.isArray(raw) ? raw : []);
      const options = (question.options ?? [])
        .map(({ option_id }) => option_id)
        .filter((option) => selected.has(option));
      if (selected.size !== options.length || (kind === "single_choice" && selected.size > 1)) {
        throw new Error(`请检查「${question.text}」的选项。`);
      }
      if (options.length > 0) answers.push({ question_id, kind, option_ids: options });
      else if (question.required) throw new Error(`请选择「${question.text}」的选项。`);
      continue;
    }
    const text = typeof raw === "string" ? raw.trim() : "";
    if (!text) {
      if (question.required) throw new Error(`请回答「${question.text}」。`);
      continue;
    }
    if (kind === "text") {
      if (round.stage === "destination_search") prepareDestination(text);
      if ([...text].length > 4000) throw new Error("每题答案最多 4,000 字符。");
      answers.push({ question_id, kind, text });
    } else if (kind === "date") {
      const date = new Date(text);
      if (!/^\d{4}-\d{2}-\d{2}$/u.test(text) || !Number.isFinite(date.valueOf()) ||
        date.toISOString().slice(0, 10) !== text) {
        throw new Error(`请为「${question.text}」填写有效日期。`);
      }
      answers.push({ question_id, kind, value: text });
    } else {
      const value = Number(text);
      if (!/^\d+$/u.test(text) || !Number.isSafeInteger(value) || value < minimum(question)) {
        throw new Error("请检查填写的整数。");
      }
      answers.push({ question_id, kind: "integer", value });
    }
  }
  return { answers };
}
