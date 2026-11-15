"use client";

import { useState, type FormEvent } from "react";
import { MapPin } from "lucide-react";
import { questionnaireAnswers, visibleQuestions, type Question, type Questionnaire, type QuestionnaireSubmission, type QuestionValues } from "@/lib/questionnaire";

const stages: Record<Questionnaire["stage"], { title: string; submit: string }> = {
  destination_search: { title: "补充目的地信息", submit: "重新查找" },
  destination_confirmation: { title: "确认调研目的地", submit: "确认目的地" },
  research: { title: "设置调研方向", submit: "继续调研" },
  travel: { title: "补充出行偏好", submit: "继续调研" },
};

export function QuestionnaireForm({ round, busy, onSubmit }: {
  round: Questionnaire;
  busy: boolean;
  onSubmit: (body: QuestionnaireSubmission) => void;
}) {
  const [values, setValues] = useState<QuestionValues>({});
  const [error, setError] = useState("");
  const { title, submit } = stages[round.stage];
  const questions = visibleQuestions(round, values);
  const canSkip = questions.every(({ required }) => !required);

  function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try { onSubmit(questionnaireAnswers(round, values)); }
    catch (problem) { setError(problem instanceof Error ? problem.message : "请检查答复。"); }
  }

  return <form className="panel clarification-form questionnaire" onSubmit={send} aria-labelledby="questionnaire-title">
    <h2 id="questionnaire-title">{title}</h2>
    <fieldset className="questionnaire-fields" disabled={busy}>
      {questions.map((question) => <QuestionField key={question.question_id} question={question} candidates={round.candidates}
        value={values[question.question_id]} onChange={(value) => {
          setValues((previous) => ({ ...previous, [question.question_id]: value }));
          setError("");
        }} />)}
      <div className="questionnaire-actions">
        <button className="button primary" type="submit">{busy ? "正在提交…" : submit}</button>
        {canSkip && <button className="button secondary" type="button" onClick={() => onSubmit({ answers: [] })}>跳过本轮</button>}
      </div>
    </fieldset>
    {error && <p className="error-text" role="alert">{error}</p>}
  </form>;
}

function QuestionField({ question, candidates, value, onChange }: {
  question: Question;
  candidates: Questionnaire["candidates"];
  value?: string | string[];
  onChange: (value: string | string[]) => void;
}) {
  const { question_id, required, text, kind } = question;
  if (kind === "single_choice" || kind === "multi_choice") {
    const selected = Array.isArray(value) ? value : value ? [value] : [];
    return <fieldset className="question-options">
      <legend>{text}{required && <span className="question-required">必填</span>}</legend>
      {(question.options ?? []).map(({ option_id, label }) => {
        const candidate = candidates?.[option_id];
        const { province, city, district } = candidate?.administrative ?? {};
        const address = [...new Set([province, city, district, candidate?.address].filter(Boolean))].join(" ");
        return <label className="question-choice" key={option_id}>
          <input name={question_id} type={kind === "single_choice" ? "radio" : "checkbox"} value={option_id}
            required={required && kind === "single_choice"} checked={selected.includes(option_id)} onChange={(event) =>
              onChange(kind === "single_choice" ? option_id : event.target.checked ?
                [...selected, option_id] : selected.filter((id) => id !== option_id))} />
          {candidate && <MapPin size={16} aria-hidden />}
          <span>{label}{address && <small>{address}</small>}</span>
        </label>;
      })}
      {kind === "single_choice" && !required && selected.length > 0 &&
        <button type="button" className="text-link" onClick={() => onChange("")}>清除选项</button>}
    </fieldset>;
  }
  const props = {
    name: question_id, required, value: typeof value === "string" ? value : "",
    onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => onChange(event.target.value),
  };
  return <label className="question-input">
    <span>{text}{required && <span className="question-required">必填</span>}</span>
    {kind === "text" ? <textarea {...props} rows={2} maxLength={4000} /> :
      <input {...props} type={kind === "date" ? "date" : "number"} min={kind === "integer" ? 0 : undefined}
        step={kind === "integer" ? 1 : undefined} />}
  </label>;
}
