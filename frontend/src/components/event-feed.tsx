"use client";

import Link from "next/link";
import { Activity, FileText, ListFilter, MessagesSquare, Radio } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ExpertAvatar, ExpertLabel } from "./expert-identity";
import { EvidenceCard } from "./evidence-card";
import type { Connection } from "./use-task-session";
import { expertName } from "@/lib/experts";
import { formatTime, reportHref, taskStatusLabels, type AgentEvent } from "@/lib/research";

export function EventFeed({ events, taskId, toolNames, agent, onSelectAgent, connection }: {
  events: AgentEvent[];
  taskId: string;
  toolNames: Map<string, string>;
  agent: string;
  onSelectAgent: (id: string) => void;
  connection: Connection;
}) {
  const [messagesOnly, setMessagesOnly] = useState(false);
  const body = useRef<HTMLOListElement>(null);
  const follow = useRef(true);
  const agentIds = Array.from(new Set([
    ...(agent ? [agent] : []),
    ...events.flatMap(({ payload }) => "agent_id" in payload ? [payload.agent_id] : []),
  ]));
  const shown = events.filter(({ payload }) =>
    (!agent || ("agent_id" in payload && payload.agent_id === agent)) &&
    (!messagesOnly || payload.type === "agent.message" || payload.type === "task.clarification"));
  useEffect(() => {
    const list = body.current;
    if (list && follow.current) list.scrollTop = list.scrollHeight;
  }, [shown.length, messagesOnly, agent]);

  return (
    <section className="panel event-panel" aria-labelledby="event-title">
      <div className="panel-heading">
        <span className="icon-tile"><MessagesSquare size={18} aria-hidden /></span><h2 id="event-title">Agent 对话与事件</h2>
        <span className={`connection ${connection}`} role="status"><Radio size={14} aria-hidden />
          {{ connecting: "连接中", open: "已连接", reconnecting: "正在重连", closed: "回放结束", unavailable: "无法连接" }[connection]}
        </span>
      </div>
      <div className="event-toolbar">
        <div className="segmented" aria-label="事件类型">
          <button type="button" aria-pressed={!messagesOnly} onClick={() => { follow.current = true; setMessagesOnly(false); }}>全部事件</button>
          <button type="button" aria-pressed={messagesOnly} onClick={() => { follow.current = true; setMessagesOnly(true); }}>仅看对话</button>
        </div>
        <label className="agent-filter"><ListFilter size={15} aria-hidden />
          <select aria-label="按专家筛选" value={agent} onChange={(event) => { follow.current = true; onSelectAgent(event.target.value); }}>
            <option value="">全部专家</option>{agentIds.map((id) => <option key={id} value={id}>{expertName(id)}</option>)}
          </select>
        </label>
      </div>
      <ol className="event-list" ref={body} aria-label="实时事件记录" role="log" onScroll={(event) => {
        const list = event.currentTarget;
        follow.current = list.scrollHeight - list.scrollTop - list.clientHeight < 32;
      }}>
        {shown.length === 0 && <li className="empty-feed">当前没有符合条件的事件。</li>}
        {shown.map((event) => {
          const { payload } = event;
          const id = "agent_id" in payload ? payload.agent_id : null;
          return <li key={event.event_id} className={payload.type === "agent.message" ? "message event-item" : "event-item"}>
            {id ? <ExpertAvatar id={id} /> : <span className="icon-tile"><Activity size={16} aria-hidden /></span>}
            <div className="event-content">
              <div className="event-meta">
                {id ? <ExpertLabel id={id} /> : <strong>调研系统</strong>}
                <time dateTime={event.occurred_at}>{formatTime(event.occurred_at)}</time>
              </div>
              <EventDetails event={event} taskId={taskId} toolNames={toolNames} />
            </div>
          </li>;
        })}
      </ol>
    </section>
  );
}

function EventDetails({ event: { payload }, taskId, toolNames }: { event: AgentEvent; taskId: string; toolNames: Map<string, string> }) {
  switch (payload.type) {
    case "task.status": return <p>{taskStatusLabels[payload.status]}{payload.error && ` · ${payload.error.message}`}</p>;
    case "task.clarification": return <ul>{payload.questions.map((question, index) => <li key={index}>{question}</li>)}</ul>;
    case "plan.ready": return <p>已组建专家团队，分配 {payload.plan.assignments.length} 项任务。</p>;
    case "agent.started": return <p>开始执行任务。</p>;
    case "agent.message": return <p>{payload.message}</p>;
    case "agent.finished": return <p>{payload.status === "completed" ? "任务执行完成。" : `任务执行失败${payload.error ? ` · ${payload.error.message}` : "。"}`}</p>;
    case "tool.started": return <p>调用工具 · {payload.tool_name}</p>;
    case "tool.finished": return <p>{toolNames.get(payload.tool_call_id) ?? "工具调用"} · {payload.status === "completed" ? "完成" : "失败"} · {payload.duration_ms} ms{payload.error && ` · ${payload.error.message}`}</p>;
    case "evidence.added": return <div className="evidence-event">
      <p>已添加 {payload.evidence_ids.length} 条证据。</p>
      {payload.evidence_ids.map((id, index) => <EvidenceDisclosure key={id} label={`查看证据 ${index + 1}`} taskId={taskId} id={id} />)}
    </div>;
    case "report.ready": return <Link className="text-link" href={reportHref(taskId, payload.report_id)}><FileText size={15} aria-hidden /> 查看第 {payload.version} 版报告</Link>;
    case "report.reviewed": return <p>{payload.decision === "approved" ? "报告审核通过。" : "审核要求修订，报告已进入返工。"} <Link className="text-link" href={reportHref(taskId, payload.report_id)}>查看该版本</Link></p>;
  }
}

function EvidenceDisclosure({ label, taskId, id }: { label: string; taskId: string; id: string }) {
  const [open, setOpen] = useState(false);
  return <details onToggle={(event) => setOpen(event.currentTarget.open)}><summary>{label}</summary>{open && <EvidenceCard taskId={taskId} evidenceId={id} />}</details>;
}
