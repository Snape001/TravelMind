import { buildResearch, type MockResearch } from "./mock-research";
import { uuidPattern, type AgentEvent, type EventPayload, type PendingClarification, type ResearchBrief, type TaskRead } from "../../src/lib/research";

type Operation = "pause" | "resume" | "answer";
type Failure = "IDEMPOTENCY_CONFLICT" | "TASK_STATE_CONFLICT" | "CLARIFICATION_STALE" | "VALIDATION_ERROR";
type Result =
  | { status: 200 | 202; task: TaskRead; error?: never }
  | { status: 409 | 422; error: Failure; task?: never };
type Step = { at: number; payload: EventPayload; round?: PendingClarification };

export const fixtureResearch = (brief: ResearchBrief, now = Date.now()) => buildResearch(brief, now);

export class FixtureTask {
  private task: TaskRead;
  private events: AgentEvent[] = [];
  private steps: Step[];
  private rest: Step[];
  private pauseAt: number | null = null;
  private stoppedAt: number | null = null;
  private readonly round: PendingClarification;
  private commands = new Map<string, { fingerprint: string; result: Result }>();

  constructor(readonly research: MockResearch) {
    const start = Date.parse(research.task.created_at);
    this.task = { ...research.task, pause_requested_at: null, pending_clarification: null };
    this.round = {
      clarification_id: crypto.randomUUID(),
      questions: ["你计划安排几天出行？", "你偏好哪种交通方式？"].map((text) => ({ question_id: crypto.randomUUID(), text })),
    };
    const steps = research.events.map(({ payload, occurred_at }) => ({ at: Date.parse(occurred_at), payload }));
    this.steps = steps.slice(0, 4);
    this.steps.push(
      { at: start + 2800, payload: { type: "task.clarification", questions: this.round.questions.map(({ text }) => text) } },
      { at: start + 2800, payload: { type: "task.status", status: "needs_clarification" }, round: this.round },
    );
    this.rest = steps.slice(4).map(({ at, payload }) => ({ at: at - start, payload }));
    this.advance(start);
  }

  private publish({ payload, at, round }: Step) {
    const timestamp = new Date(at).toISOString();
    this.events.push({
      version: 1, event_id: crypto.randomUUID(), task_id: this.task.task_id, trace_id: this.task.trace_id,
      sequence: this.events.length + 1, occurred_at: timestamp, payload,
    });
    if (payload.type === "task.status") {
      const waiting = payload.status === "paused" || payload.status === "needs_clarification";
      this.task = { ...this.task, status: payload.status, updated_at: timestamp,
        pause_requested_at: waiting || payload.status === "completed" || payload.status === "failed" ? null : this.task.pause_requested_at,
        pending_clarification: round ?? null };
      if (waiting) this.stoppedAt = at;
      if (this.task.pause_requested_at == null) this.pauseAt = null;
    }
  }

  private advance(now: number) {
    while (this.stoppedAt === null) {
      const next = this.steps[0];
      if (this.pauseAt !== null && this.pauseAt <= now && (!next || this.pauseAt < next.at)) {
        this.publish({ at: this.pauseAt, payload: { type: "task.status", status: "paused" } });
      } else if (next && next.at <= now) {
        this.steps.shift();
        this.publish(next);
      } else break;
    }
  }

  snapshot(now = Date.now()) {
    this.advance(now);
    return this.task;
  }

  eventsAt(now = Date.now()) {
    this.advance(now);
    return this.events;
  }

  command(operation: Operation, key: string, input: unknown, now = Date.now()): Result {
    if (!/^[!-~]{1,128}$/u.test(key)) return { status: 422, error: "VALIDATION_ERROR" };
    let roundId: string | undefined;
    let fingerprint = "";
    let ids: string[] = [];
    if (operation === "answer") {
      const parsed = parseAnswers(input);
      if (!parsed) return { status: 422, error: "VALIDATION_ERROR" };
      ({ roundId, fingerprint, ids } = parsed);
    }
    const scope = `${operation}:${key}`;
    const previous = this.commands.get(scope);
    if (previous) return previous.fingerprint === fingerprint ? previous.result : { status: 409, error: "IDEMPOTENCY_CONFLICT" };
    this.advance(now);
    const { status, pause_requested_at, pending_clarification } = this.task;
    let statusCode: 200 | 202 = 202;
    if (operation === "pause") {
      if (status === "queued") {
        statusCode = 200;
        this.publish({ at: now, payload: { type: "task.status", status: "paused" } });
      } else if (status === "running") {
        if (!pause_requested_at) {
          const timestamp = new Date(now).toISOString();
          this.task = { ...this.task, pause_requested_at: timestamp, updated_at: timestamp };
          this.pauseAt = now + 750;
        }
      } else return { status: 409, error: "TASK_STATE_CONFLICT" };
    } else {
      if (operation === "resume" && status !== "paused") return { status: 409, error: "TASK_STATE_CONFLICT" };
      if (operation === "answer" && (!pending_clarification || pending_clarification.clarification_id !== roundId)) {
        return { status: 409, error: "CLARIFICATION_STALE" };
      }
      if (operation === "answer" && (ids.length !== this.round.questions.length || this.round.questions.some(({ question_id }) => !ids.includes(question_id)))) {
        return { status: 422, error: "VALIDATION_ERROR" };
      }
      const elapsed = now - (this.stoppedAt ?? now);
      this.steps = [{ at: now + 450, payload: { type: "task.status", status: "running" } },
        ...(operation === "answer"
          ? this.rest.map((step) => ({ ...step, at: now + step.at }))
          : this.steps.filter(({ payload }) => payload.type !== "task.status" || payload.status !== "running")
            .map((step) => ({ ...step, at: Math.max(now + 451, step.at + elapsed + 450) })))];
      this.stoppedAt = null;
      this.publish({ at: now, payload: { type: "task.status", status: "queued" } });
    }
    const result = { status: statusCode, task: this.task };
    this.commands.set(scope, { fingerprint, result });
    return result;
  }
}

function parseAnswers(input: unknown) {
  if (!input || typeof input !== "object" || !("roundId" in input) || !("body" in input) ||
    typeof input.roundId !== "string" || !uuidPattern.test(input.roundId) || !input.body ||
    typeof input.body !== "object" || !("answers" in input.body) || Object.keys(input.body).length !== 1 ||
    !Array.isArray(input.body.answers) || !input.body.answers.length) return null;
  const answers: { question_id: string; text: string }[] = [];
  const ids: string[] = [];
  for (const entry of input.body.answers) {
    if (!entry || typeof entry !== "object" || Object.keys(entry).length !== 2 ||
      typeof entry.question_id !== "string" || !uuidPattern.test(entry.question_id) || typeof entry.text !== "string") return null;
    const id = entry.question_id.toLowerCase();
    const text = entry.text.trim();
    if (!text || [...text].length > 4000 || ids.includes(id)) return null;
    ids.push(id);
    answers.push({ question_id: id, text });
  }
  const roundId = input.roundId.toLowerCase();
  const fingerprint = JSON.stringify([roundId, answers.sort((a, b) => a.question_id.localeCompare(b.question_id))]);
  return { roundId, fingerprint, ids };
}
