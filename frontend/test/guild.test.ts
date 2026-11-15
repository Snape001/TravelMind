import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import { readEvent, summarize } from "../src/lib/agent-events";
import { demo, demoResearch } from "../src/lib/demo";
import { expertName, expertTitle, findExpert, guildExperts, tiers } from "../src/lib/experts";
import { reportDiscussion } from "../src/lib/report-discussion";
import type { AgentEvent } from "../src/lib/research";

const roster = {
  research_director: ["程策远", "首席研究总监", "leadership"],
  history_culture: ["沈观澜", "历史文化专家", "council"],
  natural_geography: ["顾山河", "自然地理专家", "council"],
  tourism_resources: ["林景川", "旅游资源专家", "council"],
  travel_planning: ["陆知行", "旅游规划专家", "council"],
  social_sentiment: ["苏听雨", "社交舆情专家", "council"],
  literature_research: ["温文渊", "文献研究专家", "council"],
  internet_research: ["谢行舟", "互联网研究专家", "council"],
  report_writer: ["叶知秋", "首席执笔官", "editorial"],
  reviewer: ["柳怀真", "监察审议官", "editorial"],
};
const portraitFiles: Record<string, string> = { tourism_resources: "lin-jingchuan", travel_planning: "tourism_resources" };

test("guild publishes each expert in the right tier with a distinct local portrait", () => {
  assert.deepEqual(guildExperts.map(({ id }) => id).sort(), Object.keys(roster).sort());
  assert.deepEqual(tiers.map(({ id }) => guildExperts.filter(({ tier }) => tier === id).length), [1, 7, 2]);

  for (const [id, [name, title, tier]] of Object.entries(roster)) {
    const expert = findExpert(id);
    assert.ok(expert);
    assert.deepEqual([expert.name, expert.title, expert.tier], [name, title, tier]);
    assert.equal(expertName(id), name);
    assert.equal(expertTitle(id), title);
    assert.ok(expert.description && expert.fields.length && expert.tools.length);
    assert.equal(expert.portrait, `/images/experts/${portraitFiles[id] ?? id}.webp`);
    const image = readFileSync(`public${expert.portrait}`);
    assert.equal(image.toString("ascii", 8, 12), "WEBP");
  }
  assert.equal(findExpert("tourism_resources")?.honorific, "山海勘游使");
  assert.ok(findExpert("tourism_resources")?.skills?.includes("游览资源与现场条件"));
});

test("an existing tourism resources assignment and v1 message resolve to Lin's profile", () => {
  const base = demoResearch.events[0];
  const agent_id = "tourism_resources";
  const assignment_id = randomUUID();
  const run_id = randomUUID();
  const payloads: AgentEvent["payload"][] = [
    { type: "plan.ready", plan: { objective: "核对景点", assignments: [{ agent_id, assignment_id, objective: "核对预约" }] } },
    { type: "agent.started", agent_id, assignment_id, run_id },
    { type: "agent.message", agent_id, run_id, message: "正在核对景区开放与预约条件。" },
  ];
  const events = payloads.map((payload, index) => {
    const sequence = index + 1;
    const event = readEvent(JSON.stringify({ ...base, event_id: randomUUID(), sequence, payload }),
      payload.type, String(sequence));
    assert.ok(event);
    return event;
  });
  const view = summarize(demoResearch.task, events);
  assert.equal(view.assignments.find((item) => item.agent_id === agent_id)?.status, "running");
  assert.equal(view.agents.get(agent_id)?.runId, run_id);
  const { payload } = events[2];
  assert.equal(payload.type, "agent.message");
  assert.equal(expertName(payload.agent_id), "林景川");
  assert.equal(findExpert(payload.agent_id)?.portrait, "/images/experts/lin-jingchuan.webp");
});

test("existing and unrecognized agent IDs retain their live identities and statuses", () => {
  const view = summarize(demoResearch.task, demoResearch.events);
  assert.ok(view.assignments.some(({ agent_id, status }) => agent_id === "travel_planning" && status === "completed"));
  assert.equal(expertName("new_expert"), "new_expert");
  assert.equal(findExpert("__proto__"), undefined);
  assert.equal(expertTitle("new_expert"), undefined);
  assert.equal(findExpert("writer"), findExpert("report_writer"));
  assert.equal(expertName("writer"), "叶知秋");
  assert.equal(findExpert("planner")?.tier, "leadership");
});

test("only explicit report headings receive consensus or dispute treatment", () => {
  assert.equal(reportDiscussion("专家共识"), "consensus");
  assert.equal(reportDiscussion("6. 专家共识与观点分歧"), "discussion");
  assert.equal(reportDiscussion("七、 争议与待核事项"), "dispute");
  assert.equal(reportDiscussion("## 观点分歧"), "dispute");
  assert.equal(reportDiscussion("预约规则与社交媒体争议考察"), undefined);
  assert.equal(reportDiscussion("调研限制与后续核验"), undefined);
});

test("public example illustrates both discussion types with existing authors and citations", () => {
  const latest = demoResearch.reports.find(({ report_id }) => report_id === demo.reportId);
  assert.ok(latest);
  const sections = latest.content.sections.filter(({ heading }) => reportDiscussion(heading));
  assert.deepEqual(sections.map(({ heading }) => reportDiscussion(heading)), ["consensus", "dispute"]);
  const sourceIds = new Set(demoResearch.evidence.map(({ evidence_id }) => evidence_id));
  for (const section of sections) {
    assert.ok(section.author_agent_ids.every(findExpert));
    assert.ok(section.evidence_ids?.length);
    assert.ok(section.evidence_ids.every((id) => sourceIds.has(id)));
  }
});
