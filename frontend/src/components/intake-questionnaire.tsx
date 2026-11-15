"use client";

import Link from "next/link";
import { ArrowLeft, ArrowRight, Check, MapPin } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  buildSubmission, checkTravelDates, minimum, questionnairePages, visibleAnswers,
  type Answers, type Destination, type Intake, type Question, type Questionnaire, type Submission,
} from "@/lib/intake";

const stages = ["确认目的地", "研究方向", "出行偏好"];

function area(place: Destination) {
  const { province, city, district } = place.administrative;
  return [...new Set([province, city, district].filter(Boolean))].join(" · ");
}

export function IntakeQuestionnaire({ round, intake, busy, onSubmit }: {
  round: Questionnaire;
  intake: Intake;
  busy: boolean;
  onSubmit: (body: Submission) => void;
}) {
  const [answers, setAnswers] = useState<Answers>({});
  const [step, setStep] = useState(0);
  const [error, setError] = useState("");
  const heading = useRef<HTMLHeadingElement>(null);
  const pages = questionnairePages(round, answers);
  const page = pages[step];
  const stage = round.stage === "research" ? 1 : round.stage === "travel" ? 2 : 0;
  const lastStep = step === pages.length - 1;

  useEffect(() => { heading.current?.focus(); }, [step]);

  function advance(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      if (round.stage === "travel" && step === 0) checkTravelDates(answers);
      if (!lastStep) setStep(step + 1);
      else onSubmit(buildSubmission(round, answers));
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "请检查答复。");
    }
  }

  function update(question: Question, value: Answers[string]) {
    setError("");
    setAnswers((previous) => visibleAnswers(round, { ...previous, [question.question_id]: value }));
  }

  const submitLabel = round.stage === "destination_search" ? "重新搜索"
    : round.stage === "destination_confirmation" ? "确认目的地"
      : round.stage === "research" && Array.isArray(answers.interests) && answers.interests.includes("travel_route")
        ? "填写出行偏好" : "提交并开始调研";

  return (
    <form className="panel intake-round" onSubmit={advance} aria-labelledby="intake-page-title">
      <ol className="intake-progress" aria-label="调研需求进度">
        {stages.map((title, index) => <li key={title} aria-current={index === stage ? "step" : undefined}
          data-complete={index < stage}>
          <span aria-hidden>{index < stage ? <Check size={14} /> : index + 1}</span>
          {title}{index === 2 && <small>按需</small>}
        </li>)}
      </ol>
      <div className="intake-round-body">
        {intake.destination && <p className="intake-location">
          <MapPin size={16} aria-hidden />{intake.destination.name}
          {area(intake.destination) && <small>{area(intake.destination)}</small>}
        </p>}
        {pages.length > 1 && <p className="intake-count">第 {step + 1} 步，共 {pages.length} 步</p>}
        <h2 id="intake-page-title" ref={heading} tabIndex={-1}>{page.title}</h2>
        {round.stage === "destination_confirmation" && <p className="muted">请核对行政区与地址。地点数据来自高德地图。</p>}
        {round.stage === "research" && page.questions[0].question_id === "interests"
          && <p className="muted">可多选。想让专家规划行程时，选择「旅游路线」。</p>}
        {round.stage === "travel" && <p className="muted">这些偏好均可选填。</p>}
        <fieldset className="intake-questions" disabled={busy}>
          {page.questions.map((question) => <QuestionField key={question.question_id} question={question}
            candidates={round.candidates} value={answers[question.question_id]}
            headingLabel={round.stage !== "travel"} setValue={(value) => update(question, value)}
            refinement={round.stage === "destination_search"} />)}
        </fieldset>
        {error && <p className="error-text" role="alert">{error}</p>}
      </div>
      <div className="intake-footer">
        {stage === 0 && <Link className="text-link" href="/"><ArrowLeft size={16} aria-hidden /> 修改目的地</Link>}
        {step > 0 && <button type="button" className="text-link" disabled={busy} onClick={() => {
          setError(""); setStep(step - 1);
        }}><ArrowLeft size={16} aria-hidden /> 上一步</button>}
        <div className="intake-actions">
          {round.stage === "travel" && <button type="button" className="button secondary" disabled={busy}
            onClick={() => onSubmit({ answers: [] })}>跳过出行偏好</button>}
          <button className="button primary" type="submit" disabled={busy}>
            {busy ? "正在提交…" : lastStep ? submitLabel : "下一步"}<ArrowRight size={16} aria-hidden />
          </button>
        </div>
      </div>
    </form>
  );
}

function QuestionField({ question, candidates, value, headingLabel, setValue, refinement }: {
  question: Question;
  candidates?: Record<string, Destination>;
  value?: Answers[string];
  headingLabel: boolean;
  setValue: (value: Answers[string]) => void;
  refinement: boolean;
}) {
  const { question_id, kind, required, text, options } = question;
  const stringValue = typeof value === "string" ? value : "";
  const selected = Array.isArray(value) ? value : [];
  const label = headingLabel ? { "aria-labelledby": "intake-page-title" } : { "aria-label": text };

  if (kind === "single_choice" || kind === "multi_choice") {
    const single = kind === "single_choice";
    return <fieldset className="intake-choice-field" {...(headingLabel ? label : {})}>
      {!headingLabel && <legend>{text}</legend>}
      <div className="intake-choice-grid">
        {options?.map((option) => {
          const id = option.option_id;
          const place = candidates?.[id];
          const active = selected.includes(id);
          return <label className="intake-choice" key={id}>
            <input type={single ? "radio" : "checkbox"} name={question_id} value={id}
              checked={active} required={single && required}
              onChange={() => setValue(single ? [id] : active ? selected.filter((item) => item !== id) : [...selected, id])} />
            <span className="intake-choice-copy">
              <strong>{option.label}</strong>
              {place && <small>{[area(place), place.address].filter(Boolean).join(" · ") || "地址未提供"}</small>}
            </span>
            <span className="intake-check" aria-hidden>{active && <Check size={15} />}</span>
          </label>;
        })}
      </div>
      {single && !required && selected.length > 0 && <button className="text-link intake-clear"
        type="button" onClick={() => setValue([])}>清除选择</button>}
    </fieldset>;
  }

  const props = { ...label, name: question_id, required, value: stringValue };
  return <label className="intake-text-field">
    {!headingLabel && <span>{text}</span>}
    {kind === "text" && !refinement
      ? <textarea {...props} rows={4} maxLength={4000}
        placeholder={question_id === "additional_notes" ? "例如，重点核对无障碍设施和预约限制" : undefined}
        onChange={(event) => setValue(event.target.value)} />
      : <input {...props} type={kind === "date" ? "date" : kind === "integer" ? "number" : "text"}
        min={kind === "integer" ? minimum(question) : undefined} step={kind === "integer" ? 1 : undefined}
        placeholder={refinement ? "例如，泉州市鲤城区开元寺" : undefined}
        onChange={(event) => setValue(event.target.value)} />}
  </label>;
}
