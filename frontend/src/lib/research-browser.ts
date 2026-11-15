import { readEnvelope, record } from "./agent-events";
import { canonicalAgentId } from "./experts";
import type { components } from "./phase4.generated";
import { uuidPattern, type AgentEvent, type EvidenceRead } from "./research";

export type ResearchEvent = components["schemas"]["ResearchBrowserEvent"];
export type ResearchSource = components["schemas"]["ResearchSourceRead"];
type Payload = ResearchEvent["payload"];
type SourceEvent = Exclude<Payload, { type: "agent.usage" }>;
type SourceAction = Omit<ResearchEvent, "payload"> & { payload: SourceEvent };
export type FeedEvent = AgentEvent | SourceAction;
type SourcePayload<T> = Extract<SourceEvent, { type: T }>;
type Score = SourcePayload<"research.source.scored">;
type Citation = SourcePayload<"research.source.cited">;
export type AgentUsage = components["schemas"]["AgentUsage"];
export type UsageFor = (agentId: string, runId: string, messageId?: string) => AgentUsage[];

export const sourceTypes: Record<EvidenceRead["source_type"], string> = {
  web: "网页", official: "官方资料", academic: "论文", book: "图书", social: "社交内容", map: "地图",
};
export const qualityLabels: Record<Score["level"], string> = { high: "高", medium: "中", low: "低" };
export const fetchLabels = { started: "抓取中", succeeded: "抓取完成", failed: "抓取失败", skipped: "跳过抓取" };

const isUuid = (value: unknown) => typeof value === "string" && uuidPattern.test(value);
const optionalText = (value: unknown) => value == null || typeof value === "string";
const nonnegative = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const positive = (value: unknown) => nonnegative(value) && value > 0;

export function readResearchEvent(data: string, name: string, id: string): ResearchEvent | null {
  const event = readEnvelope(data, name, id);
  if (!event || !isUuid(event.event_id) || !isUuid(event.task_id) || !isUuid(event.trace_id)) return null;
  const p = event.payload;
  if (typeof p.agent_id !== "string" || !isUuid(p.run_id) || !validPayload(p)) return null;
  return event as unknown as ResearchEvent;
}

function validPayload(p: Record<string, unknown>) {
  if (p.type === "agent.usage") {
    const u = p.usage;
    return record(u) && isUuid(u.call_id) && (u.message_event_id == null || isUuid(u.message_event_id)) &&
      typeof u.provider === "string" && typeof u.model === "string" &&
      optionalText(u.prompt_preview) && (typeof u.prompt_preview !== "string" || [...u.prompt_preview].length <= 280) &&
      (u.input_tokens === null || nonnegative(u.input_tokens)) && (u.output_tokens === null || nonnegative(u.output_tokens));
  }
  if (!isUuid(p.source_id)) return false;
  switch (p.type) {
    case "research.source.discovered":
      return typeof p.source_type === "string" && Object.hasOwn(sourceTypes, p.source_type) &&
        typeof p.title === "string" && optionalText(p.publisher) && optionalText(p.source_url);
    case "research.source.fetch":
      return typeof p.status === "string" && Object.hasOwn(fetchLabels, p.status) &&
        positive(p.attempt) && optionalText(p.error_code);
    case "research.source.scored":
      return isUuid(p.evidence_id) && isUuid(p.assignment_id) &&
        (p.policy_version === undefined || p.policy_version === "v1") &&
        typeof p.level === "string" && Object.hasOwn(qualityLabels, p.level) && nonnegative(p.total_score) && p.total_score <= 10;
    case "research.source.cited":
      return isUuid(p.evidence_id) && isUuid(p.report_id) && positive(p.report_version);
    default:
      return false;
  }
}

export type BrowserSource = {
  discovery: SourcePayload<"research.source.discovered">;
  fetch?: SourcePayload<"research.source.fetch">;
  scores: Score[];
  citation?: Citation;
  evidenceIds: Set<string>;
};

export function projectResearch(events: ResearchEvent[], publishedId: string | undefined) {
  const sources = new Map<string, BrowserSource>();
  const byRun = new Map<string, AgentUsage[]>();
  const calls = new Set<string>();
  const evidenceIds = new Set<string>();
  const actions: SourceAction[] = [];
  let latestCitation: Citation | undefined;
  for (const event of events) {
    const { payload } = event;
    if (payload.type === "agent.usage") {
      const { usage } = payload;
      if (calls.has(usage.call_id)) continue;
      calls.add(usage.call_id);
      const key = usageKey(payload.agent_id, payload.run_id);
      const group = byRun.get(key) ?? [];
      group.push(usage);
      byRun.set(key, group);
      continue;
    }
    actions.push({ ...event, payload });
    if (payload.type === "research.source.discovered") {
      const previous = sources.get(payload.source_id);
      sources.set(payload.source_id, previous ? { ...previous, discovery: payload } : {
        discovery: payload, scores: [], evidenceIds: new Set(),
      });
    } else if (payload.type === "research.source.cited" &&
      (!latestCitation || payload.report_version >= latestCitation.report_version)) latestCitation = payload;
  }
  const reportId = publishedId ?? latestCitation?.report_id;
  for (const { payload } of actions) {
    const source = sources.get(payload.source_id);
    if (!source) continue;
    switch (payload.type) {
      case "research.source.fetch":
        if (!source.fetch || payload.attempt >= source.fetch.attempt) source.fetch = payload;
        break;
      case "research.source.scored":
        source.scores = source.scores.filter((score) => score.assignment_id !== payload.assignment_id || score.evidence_id !== payload.evidence_id);
        source.scores.push(payload);
        source.evidenceIds.add(payload.evidence_id);
        evidenceIds.add(payload.evidence_id);
        break;
      case "research.source.cited":
        if (payload.report_id === reportId) source.citation = payload;
        source.evidenceIds.add(payload.evidence_id);
        evidenceIds.add(payload.evidence_id);
        break;
    }
  }
  const usageFor: UsageFor = (agentId, runId, messageId) => {
    const group = byRun.get(usageKey(agentId, runId)) ?? [];
    return messageId ? group.filter(({ message_event_id }) => message_event_id === messageId) : group;
  };
  return { sources: [...sources.values()], evidenceIds, actions, usageFor };
}

export function mergeActivity(events: AgentEvent[], actions: SourceAction[]): FeedEvent[] {
  const timeline: FeedEvent[] = [];
  let actionIndex = 0;
  for (const event of events) {
    const timestamp = Date.parse(event.occurred_at);
    while (actionIndex < actions.length && Date.parse(actions[actionIndex].occurred_at) < timestamp) {
      timeline.push(actions[actionIndex++]);
    }
    timeline.push(event);
  }
  return timeline.concat(actions.slice(actionIndex));
}

const usageKey = (agentId: string, runId: string) => `${canonicalAgentId(agentId)}:${runId}`;

export function usageTotals(calls: AgentUsage[]) {
  function sum(field: "input_tokens" | "output_tokens") {
    const values = calls.flatMap((call) => call[field] === null ? [] : [call[field]]);
    return { count: values.length ? values.reduce((total, value) => total + value, 0) : null, partial: values.length > 0 && values.length < calls.length };
  }
  return { input: sum("input_tokens"), output: sum("output_tokens") };
}

const toolActions = new Map([
  ["retrieve_evidence", "检索相关资料"], ["web_search", "检索公开网页"], ["bocha.search", "检索公开网页"],
  ["网页资料检索", "检索公开网页"], ["交通资料检索", "核对交通资料"],
  ["openalex.search", "查找学术论文"], ["firecrawl.scrape", "读取网页正文"], ["amap.route", "查询地图路线"],
]);
export const toolAction = (tool?: string) => toolActions.get(tool ?? "") ?? "查阅研究资料";
export const safeIdentifier = (value?: string | null) => value && /^[\w.-]{1,80}$/u.test(value) ? value : undefined;

export function publicSourceUrl(raw?: string | null): string | undefined {
  if (!raw || !/^https?:\/\//iu.test(raw)) return;
  try {
    const url = new URL(raw);
    const host = url.hostname.replace(/\.$/u, "");
    if (url.username || url.password || !/^https?:$/u.test(url.protocol)) return;
    if (host.startsWith("[")) {
      // Only global unicast literals, excluding the documentation prefix.
      if (!/^\[[23][\da-f]{3}:/iu.test(host) || /^\[2001:db8:/iu.test(host)) return;
    } else if (/^\d+\.\d+\.\d+\.\d+$/u.test(host)) {
      const [a, b] = host.split(".").map(Number);
      if (a === 0 || a === 10 || a === 127 || a >= 224 || (a === 100 && b >= 64 && b <= 127) ||
        (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) || (a === 192 && [0, 168].includes(b)) ||
        (a === 198 && [18, 19, 51].includes(b)) || (a === 203 && b === 0)) return;
    } else if (!host.includes(".") || /\.(?:localhost|local|internal|test|invalid|onion|home\.arpa)$/iu.test(host)) return;
    return url.href;
  } catch {
    return undefined;
  }
}
