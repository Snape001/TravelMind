"use client";

import Link from "next/link";
import { Activity, ArrowUpRight, FileText, MessagesSquare } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ExpertAvatar, ExpertLabel } from "./expert-identity";
import { UsageHover } from "./usage-hover";
import type { Connection } from "./use-task-session";
import type { SourceSelection } from "./research-browser";
import { canonicalAgentId, expertName } from "@/lib/experts";
import { formatTime, reportHref, taskStatusLabels, type AgentEvent } from "@/lib/research";
import { fetchLabels, mergeActivity, qualityLabels, safeIdentifier, sourceTypes, toolAction, type FeedEvent, type projectResearch } from "@/lib/research-browser";

type ResearchView = ReturnType<typeof projectResearch>;

const connectionLabels = { connecting: "连接中", open: "实时更新", reconnecting: "正在重连", closed: "回放结束", unavailable: "无法连接" };

export function EventFeed({ events, taskId, toolNames, agent, connection, research, controls, onSource }: {
  events: AgentEvent[];
  taskId: string;
  toolNames: Map<string, string>;
  agent: string;
  connection: Connection;
  research: ResearchView;
  controls?: ReactNode;
  onSource: (source: SourceSelection) => void;
}) {
  const [messagesOnly, setMessagesOnly] = useState(false);
  const body = useRef<HTMLOListElement>(null);
  const follow = useRef(true);
  const merged = useMemo(() => mergeActivity(events, research.actions), [events, research.actions]);
  const titles = useMemo(() => new Map(research.sources.map(({ discovery }) => [discovery.source_id, discovery.title])), [research.sources]);
  const shown = merged.filter(({ payload }) =>
    (!agent || ("agent_id" in payload && canonicalAgentId(payload.agent_id) === agent)) &&
    (!messagesOnly || payload.type === "agent.message" || payload.type === "task.clarification"));

  useEffect(() => {
    const list = body.current;
    if (list && follow.current) list.scrollTop = list.scrollHeight;
  }, [shown.length, messagesOnly, agent]);

  return (
    <section className="panel workspace-panel event-panel" aria-labelledby="event-title">
      <header className="panel-heading">
        <span className="workspace-icon"><MessagesSquare size={18} aria-hidden /></span><h2 id="event-title">专家对话</h2>
        <span className={`connection ${connection}`} role="status"><span className="live-indicator" />{connectionLabels[connection]}</span>
      </header>
      <div className="event-toolbar">
        <div className="segmented" aria-label="事件类型">
          <button type="button" aria-pressed={!messagesOnly} onClick={() => { follow.current = true; setMessagesOnly(false); }}>全部活动</button>
          <button type="button" aria-pressed={messagesOnly} onClick={() => { follow.current = true; setMessagesOnly(true); }}>仅看对话</button>
        </div>
        <span className="feed-filter">{agent ? expertName(agent) : "全部专家"}</span>
      </div>
      <div className="workspace-controls">{controls}</div>
      <ol className="event-list" ref={body} aria-label="实时事件记录" role="log" onScroll={(event) => {
        const list = event.currentTarget;
        follow.current = list.scrollHeight - list.scrollTop - list.clientHeight < 32;
      }}>
        {shown.length === 0 && <li className="empty-feed">当前没有符合条件的活动。</li>}
        {shown.map((event) => {
          const { payload } = event;
          const id = "agent_id" in payload ? payload.agent_id : null;
          const calls = "run_id" in payload ? research.usageFor(payload.agent_id, payload.run_id) : [];
          return <li key={event.event_id} className={payload.type === "agent.message" ? "message event-item" : "event-item"}>
            {id ? <UsageHover calls={calls} label={expertName(id)} focusable><ExpertAvatar id={id} /></UsageHover> :
              <span className="icon-tile"><Activity size={16} aria-hidden /></span>}
            <div className="event-content">
              <div className="event-meta">
                {id ? <ExpertLabel id={id} /> : <strong>调研系统</strong>}
                <time dateTime={event.occurred_at}>{formatTime(event.occurred_at)}</time>
              </div>
              <EventDetails event={event} taskId={taskId} toolNames={toolNames} research={research} titles={titles} onSource={onSource} />
            </div>
          </li>;
        })}
      </ol>
    </section>
  );
}

function EventDetails({ event, taskId, toolNames, research, titles, onSource }: {
  event: FeedEvent;
  taskId: string;
  toolNames: Map<string, string>;
  research: ResearchView;
  titles: Map<string, string>;
  onSource: (source: SourceSelection) => void;
}) {
  const { payload } = event;
  switch (payload.type) {
    case "task.status": return <p>{taskStatusLabels[payload.status]}{payload.error && ` · ${payload.error.message}`}</p>;
    case "task.clarification": return <details className="technical-detail clarification-history">
      <summary>调研总监已发出 {payload.questions.length} 个待答问题</summary>
      <ul>{payload.questions.map((question, index) => <li key={index}>{question}</li>)}</ul>
    </details>;
    case "plan.ready": return <p>已组建专家团队，分配 {payload.plan.assignments.length} 项任务。</p>;
    case "agent.started": return <p>开始执行任务。</p>;
    case "agent.message": return <UsageHover focusable className="message-bubble" label={expertName(payload.agent_id)}
      calls={research.usageFor(payload.agent_id, payload.run_id, event.event_id)}>{payload.message}</UsageHover>;
    case "agent.finished": return <StudyAction title={payload.status === "completed" ? "任务执行完成" : "该分工执行失败"}
      info={safeIdentifier(payload.error?.code)} />;
    case "tool.started": return <StudyAction title={toolAction(payload.tool_name)} info={safeIdentifier(payload.tool_name)} />;
    case "tool.finished": {
      const tool = toolNames.get(payload.tool_call_id);
      const status = payload.status === "completed" ? "已完成" : "失败";
      const info = [safeIdentifier(tool), status, `${payload.duration_ms} ms`, safeIdentifier(payload.error?.code)].filter(Boolean).join(" · ");
      return <StudyAction title={`${toolAction(tool)} · ${status}`} info={info} />;
    }
    case "evidence.added": return <div className="evidence-event">
      <p>已归档 {payload.evidence_ids.length} 条证据。</p>
      {payload.evidence_ids.map((id, index) => <button key={id} type="button" className="text-link" onClick={() => onSource({ evidenceId: id })}>
        查看证据 {index + 1} <ArrowUpRight size={13} aria-hidden />
      </button>)}
    </div>;
    case "report.ready": return <Link className="text-link" href={reportHref(taskId, payload.report_id)}><FileText size={15} aria-hidden /> 查看第 {payload.version} 版报告</Link>;
    case "report.reviewed": return <p>{payload.decision === "approved" ? "报告审核通过。" : "审核要求修订，报告已进入返工。"} <Link className="text-link" href={reportHref(taskId, payload.report_id)}>查看该版本</Link></p>;
    case "research.source.discovered": return <StudyAction title={`发现${sourceTypes[payload.source_type]}资料`}>
      <SourceJump id={payload.source_id} title={payload.title} open={onSource} />
    </StudyAction>;
    case "research.source.fetch": return <StudyAction title={fetchLabels[payload.status]}
      info={[`尝试 ${payload.attempt}`, safeIdentifier(payload.error_code)].filter(Boolean).join(" · ")}>
      <SourceJump id={payload.source_id} title={titles.get(payload.source_id)} open={onSource} />
    </StudyAction>;
    case "research.source.scored": return <StudyAction title={`完成证据质量评估 · ${qualityLabels[payload.level]}`}
      info={`评分 ${payload.total_score}/10 · 策略 ${payload.policy_version ?? "v1"}`}>
      <SourceJump id={payload.source_id} title={titles.get(payload.source_id)} open={onSource} />
    </StudyAction>;
    case "research.source.cited": return <StudyAction title={`第 ${payload.report_version} 版报告引用了该来源`}>
      <SourceJump id={payload.source_id} title={titles.get(payload.source_id)} open={onSource} />
    </StudyAction>;
  }
}

function SourceJump({ id, title, open }: { id: string; title?: string; open: (selection: SourceSelection) => void }) {
  return <button className="text-link" type="button" onClick={() => open({ sourceId: id })}>{title ?? "查看来源"} <ArrowUpRight size={13} aria-hidden /></button>;
}

function StudyAction({ title, info, children }: { title: string; info?: string; children?: ReactNode }) {
  return <div className="study-action">
    <p>{title}</p>{children}
    {info && <details className="technical-detail"><summary>技术详情</summary><p>{info}</p></details>}
  </div>;
}
