import type { ClarificationAnswers, PendingClarification, TaskRead, TaskStatus } from "./research";
import type { QuestionnaireSubmission } from "./questionnaire";

export type TaskCommand =
  | { operation: "pause" | "resume" }
  | { operation: "answer"; roundId: string; body: ClarificationAnswers }
  | { operation: "answer-questionnaire"; roundId: string; body: QuestionnaireSubmission };

export const isTerminal = (status: TaskStatus) => status === "completed" || status === "failed";

export function taskControls(task: TaskRead) {
  const pausing = task.status === "running" && !!task.pause_requested_at;
  return {
    pause: !pausing && (task.status === "queued" || task.status === "running"),
    resume: task.status === "paused",
    pausing,
    round: task.status === "needs_clarification" ? task.pending_clarification ?? null : null,
  };
}

export function nextSnapshot(current: TaskRead, incoming: TaskRead): TaskRead {
  const currentTime = Date.parse(current.updated_at);
  const incomingTime = Date.parse(incoming.updated_at);
  const fraction = (timestamp: string) => (timestamp.match(/\.(\d+)/u)?.[1] ?? "").padEnd(9, "0");
  if (incoming.task_id !== current.task_id || incoming.trace_id !== current.trace_id ||
    incomingTime < currentTime ||
    (incomingTime === currentTime && fraction(incoming.updated_at) < fraction(current.updated_at)) ||
    (isTerminal(current.status) && current.status !== incoming.status)) return current;
  return incoming;
}

export function makeAnswers(round: PendingClarification, values: Record<string, string>): ClarificationAnswers {
  const answers = round.questions.map(({ question_id }) => ({ question_id, text: values[question_id]?.trim() ?? "" }));
  if (answers.some(({ text }) => !text)) throw new Error("请回答每道问题。");
  if (answers.some(({ text }) => [...text].length > 4000)) throw new Error("每题答案最多 4,000 字符。");
  return { answers };
}
