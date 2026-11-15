import type { components } from "./phase4.generated";

type Schemas = components["schemas"];
export type Questionnaire = Schemas["ResearchQuestionnaire"];
export type Question = Schemas["QuestionnaireQuestion"];
export type QuestionnaireSubmission = Schemas["QuestionnaireSubmission"];
export type QuestionValues = Record<string, string | string[]>;

export function prepareDestination(input: string): Schemas["DestinationTaskCreate"] {
  const destination = input.trim();
  if (!destination || [...destination].length > 80) throw new Error("请输入 1–80 字的目的地。");
  return { destination };
}

export function visibleQuestions(round: Questionnaire, values: QuestionValues) {
  const visible = new Set<string>();
  return round.questions.filter((question) => {
    const rule = question.visible_if;
    if (rule) {
      const value = values[rule.question_id];
      if (!visible.has(rule.question_id) ||
        !(Array.isArray(value) ? value.includes(rule.includes_option_id) : value === rule.includes_option_id)) return false;
    }
    visible.add(question.question_id);
    return true;
  });
}

export function questionnaireAnswers(round: Questionnaire, values: QuestionValues): QuestionnaireSubmission {
  const answers: QuestionnaireSubmission["answers"] = [];
  for (const question of visibleQuestions(round, values)) {
    const { kind, question_id } = question;
    const raw = values[question_id];
    const value = typeof raw === "string" ? raw.trim() : raw;
    if (!value || !value.length) {
      if (question.required) throw new Error(`请回答「${question.text}」。`);
      continue;
    }
    if (kind === "single_choice" || kind === "multi_choice") {
      const options = new Set(question.options?.map(({ option_id }) => option_id));
      const selected = new Set(Array.isArray(value) ? value : [value]);
      if ([...selected].some((id) => !options.has(id)) || (kind === "single_choice" && selected.size !== 1)) {
        throw new Error(`请检查「${question.text}」的选项。`);
      }
      const option_ids = [...options].filter((id) => selected.has(id));
      answers.push({ question_id, kind, option_ids });
      continue;
    }
    if (typeof value !== "string") throw new Error(`请检查「${question.text}」的答复。`);
    if (kind === "integer") {
      const number = Number(value);
      if (!Number.isSafeInteger(number) || number < 0) throw new Error(`「${question.text}」需要填写非负整数。`);
      answers.push({ question_id, kind, value: number });
    } else if (kind === "date") {
      const date = new Date(value);
      if (!/^\d{4}-\d{2}-\d{2}$/u.test(value) || !Number.isFinite(date.valueOf()) || date.toISOString().slice(0, 10) !== value) {
        throw new Error(`请为「${question.text}」填写有效日期。`);
      }
      answers.push({ question_id, kind, value });
    } else {
      if ([...value].length > 4000) throw new Error("每题答案最多 4,000 字符。");
      answers.push({ question_id, kind, text: value });
    }
  }
  return { answers };
}
