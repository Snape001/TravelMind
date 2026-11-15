"use client";

import { Pause, Play } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { answerQuestionnaire, ApiError, readIntake, sendCommand } from "@/lib/task-client";
import type { Intake, Submission } from "@/lib/intake";
import { makeAnswers, taskControls, type TaskCommand } from "@/lib/task-lifecycle";
import type { CurrentUser, PendingClarification, TaskRead } from "@/lib/research";
import { IntakeQuestionnaire } from "./intake-questionnaire";

type Action = TaskCommand | { operation: "answer-questionnaire"; roundId: string; body: Submission };

export function TaskControls({ task, user, accept, refresh, onError }: {
  task: TaskRead;
  user: CurrentUser;
  accept: (snapshot: TaskRead) => void;
  refresh: () => void;
  onError: (error: unknown) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef<AbortController | null>(null);
  const last = useRef<{ fingerprint: string; key: string } | null>(null);
  const { pause, resume, pausing, round } = taskControls(task);

  useEffect(() => () => pending.current?.abort(), []);

  async function command(action: Action) {
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError("");
    const fingerprint = JSON.stringify(action);
    const key = last.current?.fingerprint === fingerprint ? last.current.key : crypto.randomUUID();
    last.current = { fingerprint, key };
    try {
      const result = action.operation === "answer-questionnaire"
        ? await answerQuestionnaire(task.task_id, action.roundId, action.body, key, user.csrf_token, controller.signal)
        : await sendCommand(task.task_id, action, key, user.csrf_token, controller.signal);
      if (controller.signal.aborted) return;
      accept(result);
      last.current = null;
      refresh();
    } catch (problem) {
      if (controller.signal.aborted) return;
      onError(problem);
      if (problem instanceof ApiError && (problem.status === 409 || problem.status === 422)) {
        last.current = null;
        refresh();
      }
      setError(problem instanceof Error ? problem.message : "操作失败，请重试。");
    } finally {
      pending.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return <div className="task-controls">
    <div className="task-actions">
      {pause && <button className="button secondary" type="button" disabled={busy} onClick={() => void command({ operation: "pause" })}><Pause size={16} aria-hidden /> 暂停调研</button>}
      {resume && <button className="button primary" type="button" disabled={busy} onClick={() => void command({ operation: "resume" })}><Play size={16} aria-hidden /> 恢复调研</button>}
      {pausing && <p className="notice" role="status">已请求暂停，正在等待当前步骤完成。</p>}
    </div>
    {round && <ActiveClarification key={round.clarification_id} round={round} taskId={task.task_id}
      busy={busy} onError={onError} onAnswer={(action) => void command(action)} />}
    {task.status === "needs_clarification" && !round && <div className="notice"><p>正在读取当前澄清问题。</p>
      <button className="text-link" type="button" onClick={refresh}>重新读取</button></div>}
    {error && <p className="error-text" role="alert">{error}</p>}
  </div>;
}

function ActiveClarification({ taskId, round, busy, onError, onAnswer }: {
  taskId: string;
  round: PendingClarification;
  busy: boolean;
  onError: (error: unknown) => void;
  onAnswer: (action: Action) => void;
}) {
  const [result, setResult] = useState<Intake>();
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    readIntake(taskId, controller.signal).then((intake) => {
      if (!controller.signal.aborted) setResult(intake);
    }).catch((problem: unknown) => {
      if (controller.signal.aborted) return;
      onError(problem);
      setError(problem instanceof Error ? problem.message : "无法读取问卷。");
    });
    return () => controller.abort();
  }, [taskId, onError, retry]);

  if (error) return <div className="notice"><p className="error-text" role="alert">{error}</p>
    <button className="text-link" type="button" onClick={() => { setError(""); setRetry(retry + 1); }}>
      重新读取
    </button></div>;
  if (!result || (result.questionnaire && result.questionnaire.questionnaire_id !== round.clarification_id)) {
    return <p className="notice" role="status">正在读取当前问卷…</p>;
  }
  if (result.questionnaire) {
    return <IntakeQuestionnaire intake={result} round={result.questionnaire} busy={busy}
      onSubmit={(body) => onAnswer({ operation: "answer-questionnaire", roundId: round.clarification_id, body })} />;
  }
  return <ClarificationForm round={round} busy={busy}
    onSubmit={(body) => onAnswer({ operation: "answer", roundId: round.clarification_id, body })} />;
}

function ClarificationForm({ round, busy, onSubmit }: {
  round: PendingClarification;
  busy: boolean;
  onSubmit: (answers: ReturnType<typeof makeAnswers>) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState("");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try { onSubmit(makeAnswers(round, values)); }
    catch (problem) { setError(problem instanceof Error ? problem.message : "请检查答复。"); }
  }

  return <form className="panel clarification-form" onSubmit={submit} aria-labelledby="clarification-title">
    <h2 id="clarification-title">调研总监需要补充信息</h2>
    <p className="muted">回答全部问题后继续调研。</p>
    <fieldset disabled={busy}>
      {round.questions.map(({ question_id, text }) => <label key={question_id}>
        <span>{text}</span><textarea required rows={2} value={values[question_id] ?? ""} onChange={(event) => {
          const value = event.target.value;
          setValues((previous) => ({ ...previous, [question_id]: value }));
        }} />
      </label>)}
      <button className="button primary" type="submit">{busy ? "正在提交…" : "提交答复"}</button>
    </fieldset>
    {error && <p className="error-text" role="alert">{error}</p>}
  </form>;
}
