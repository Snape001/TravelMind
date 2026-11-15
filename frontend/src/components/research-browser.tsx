"use client";

import Link from "next/link";
import { ArrowLeft, ArrowUpRight, BookOpen, FileSearch, FileText, Globe2, LibraryBig, Map, MessagesSquare } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { demo, demoResearch } from "@/lib/demo";
import { expertName } from "@/lib/experts";
import { fetchLabels, publicSourceUrl, qualityLabels, safeIdentifier, sourceTypes, type BrowserSource, type ResearchSource } from "@/lib/research-browser";
import { reportHref, type EvidenceRead } from "@/lib/research";
import { readEvidence, readResearchSource, type StreamConnection } from "@/lib/task-client";
import { ExpertLabel } from "./expert-identity";

export type SourceSelection = { sourceId: string } | { evidenceId: string };
export type LegacyEvidence = { evidenceId: string; agentId: string };
type Category = EvidenceRead["source_type"];
const icons = { web: Globe2, official: FileText, academic: BookOpen, book: LibraryBig, map: Map, social: MessagesSquare };
const connectionLabels: Record<StreamConnection, string> = {
  connecting: "正在连接", open: "实时更新", reconnecting: "正在重连", closed: "回放结束", unavailable: "历史证据",
};

export function ResearchBrowser({ taskId, sources, evidence, connection, selected, onSelect, citedIds, publishedId, onError }: {
  taskId: string;
  sources: BrowserSource[];
  evidence: LegacyEvidence[];
  connection: StreamConnection;
  selected: SourceSelection | null;
  onSelect: (source: SourceSelection | null) => void;
  citedIds: Set<string> | null;
  publishedId?: string;
  onError: (error: unknown) => void;
}) {
  const activeSource = selected && sources.find((source) => "sourceId" in selected ?
    source.discovery.source_id === selected.sourceId : source.evidenceIds.has(selected.evidenceId));
  const activeEvidence = selected && "evidenceId" in selected ? selected.evidenceId : undefined;
  const selectedKey = activeSource?.discovery.source_id ?? activeEvidence ?? (selected ? "unavailable" : "");
  const citation = (id: string) => citedIds?.has(id) ? publishedId : undefined;
  return <section className="panel workspace-panel source-panel" aria-labelledby="research-browser-title">
    <header className="panel-heading">
      <span className="workspace-icon"><FileSearch size={18} aria-hidden /></span><h2 id="research-browser-title">Research Browser</h2>
      <span className={`connection ${connection}`} role="status"><span className="live-indicator" />{connectionLabels[connection]}</span>
    </header>
    <div className="sources-toolbar">
      {selected ? <button className="text-link" type="button" onClick={() => onSelect(null)}><ArrowLeft size={15} aria-hidden /> 返回来源列表</button> :
        <><h3>调研来源</h3><span>{sources.length + evidence.length} 条</span></>}
    </div>
    <div className="source-views">
      <div className={`source-scroll${selected ? " inactive" : ""}`} inert={!!selected}>
        {sources.length || evidence.length ? <ul className="source-list">
            {sources.map((source) => {
              const { source_id, title, source_type, publisher, source_url, agent_id } = source.discovery;
              return <li key={source_id}><SourceCard title={title} category={source_type} publisher={publisher ?? hostOf(source_url)}
                agent={agent_id} onClick={() => onSelect({ sourceId: source_id })}>
                <span className={`source-badge ${source.fetch?.status ?? ""}`}>{source.fetch ? fetchLabels[source.fetch.status] : "已发现"}</span>
                <QualityBadge source={source} />
                <CitationBadge reportId={source.citation?.report_id} />
              </SourceCard></li>;
            })}
            {evidence.map(({ evidenceId, agentId }) => <li key={evidenceId}>
              <LegacyCard taskId={taskId} evidenceId={evidenceId} agentId={agentId} reportId={citation(evidenceId)}
                known={citedIds !== null} onClick={() => onSelect({ evidenceId })} onError={onError} />
            </li>)}
          </ul> : <div className="source-empty" role="status"><FileSearch size={25} aria-hidden />
            <strong>{connection === "closed" || connection === "unavailable" ? "暂无可查看的来源" : "等待专家发现来源"}</strong>
            <p>网页、论文、社交内容和地图资料会显示在这里。</p>
          </div>}
      </div>
      {selected && <div className="source-scroll" key={selectedKey}>
        {activeSource ? <SourceDetail taskId={taskId} source={activeSource} onError={onError} /> :
          activeEvidence ? <LegacyDetail taskId={taskId} evidenceId={activeEvidence}
            agentId={evidence.find((item) => item.evidenceId === activeEvidence)?.agentId}
            reportId={citation(activeEvidence)} known={citedIds !== null} onError={onError} /> :
            <p className="source-empty" role="status">该来源暂不可用。</p>}
      </div>}
    </div>
  </section>;
}

function hostOf(value?: string | null) {
  const url = publicSourceUrl(value);
  return url ? new URL(url).hostname : undefined;
}

function SourceCard({ title, category, publisher, agent, onClick, children }: {
  title: string;
  category?: Category;
  publisher?: string | null;
  agent: string;
  onClick: () => void;
  children: ReactNode;
}) {
  const Icon = category ? icons[category] : FileText;
  return <button className="source-card" type="button" onClick={onClick}>
    <span className="source-card-type"><Icon size={15} aria-hidden />{category ? sourceTypes[category] : "研究证据"}<ArrowUpRight size={14} aria-hidden /></span>
    <strong>{title}</strong>
    <span className="source-card-meta">{publisher ?? "来源未标注"}<span aria-hidden>·</span>{expertName(agent)}</span>
    <span className="source-badges">{children}</span>
  </button>;
}

function QualityBadge({ source }: { source: BrowserSource }) {
  const score = source.scores[0];
  return <span className={`source-badge ${source.scores.length === 1 ? score.level : ""}`}>
    {source.scores.length > 1 ? `${source.scores.length} 项质量评分` : score ? `质量${qualityLabels[score.level]} · ${score.total_score}/10` : "待评分"}
  </span>;
}

function CitationBadge({ reportId, known = true }: { reportId?: string; known?: boolean }) {
  return <span className={`source-badge ${reportId ? "cited" : ""}`}>{reportId ? "已引用" : known ? "未引用" : "引用待确认"}</span>;
}

function SourceDetail({ taskId, source, onError }: { taskId: string; source: BrowserSource; onError: (error: unknown) => void }) {
  const { source_id, source_type, title, publisher, source_url, agent_id } = source.discovery;
  const [record, setRecord] = useState<ResearchSource>();
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const fetch = source.fetch;

  useEffect(() => {
    if (fetch?.status === "started") return;
    const controller = new AbortController();
    readResearchSource(taskId, source_id, controller.signal).then((detail) => {
      if (!controller.signal.aborted) { setRecord(detail); setError(""); }
    }).catch((problem: unknown) => {
      if (controller.signal.aborted) return;
      onError(problem);
      setError(problem instanceof Error ? problem.message : "无法读取来源正文。");
    });
    return () => controller.abort();
  }, [taskId, source_id, fetch, onError, attempt]);

  const url = publicSourceUrl(record?.source_url ?? source_url);
  const citation = source.citation;
  return <article className="source-detail">
    <p className="eyebrow">{sourceTypes[source_type]} · {record?.publisher ?? publisher ?? hostOf(url) ?? "来源未标注"}</p>
    <h3>{record?.title ?? title}</h3>
    <ExpertLabel id={agent_id} avatar />
    <div className="source-badges">
      <span className={`source-badge ${fetch?.status ?? ""}`}>{fetch ? fetchLabels[fetch.status] : "已发现"}</span>
      <QualityBadge source={source} /><CitationBadge reportId={citation?.report_id} />
    </div>
    {fetch && <details className="technical-detail">
      <summary>抓取详情</summary><p>尝试 {fetch.attempt} · {fetchLabels[fetch.status]}{safeIdentifier(fetch.error_code) && ` · ${fetch.error_code}`}</p>
    </details>}
    {source.scores.length > 0 && <dl className="source-scores">
      {source.scores.map((score) => <div key={`${score.assignment_id}:${score.evidence_id}`}>
        <dt>{expertName(score.agent_id)}</dt><dd>{qualityLabels[score.level]} · {score.total_score}/10</dd>
      </div>)}
    </dl>}
    <div className="source-links">
      <OriginalLink url={url} />
      {citation && <ReportCitation taskId={taskId} reportId={citation.report_id} evidenceId={citation.evidence_id} />}
    </div>
    <h4>来源正文</h4>
    {fetch?.status === "started" ? <p className="muted" role="status">正在抓取正文…</p> :
      error ? <div><p className="error-text" role="alert">{error}</p>
        <button type="button" className="text-link" onClick={() => setAttempt((value) => value + 1)}>重新读取</button></div> :
        record ? <p className="source-body">{record.body_text ?? "该来源暂无可展示的正文。"}</p> :
          <p className="muted" role="status">正在读取来源…</p>}
  </article>;
}

function useLegacyEvidence(taskId: string, id: string, onError: (error: unknown) => void) {
  const [item, setItem] = useState<EvidenceRead>();
  const [error, setError] = useState("");
  useEffect(() => {
    if (taskId === demo.taskId) return;
    const controller = new AbortController();
    readEvidence(taskId, id, controller.signal).then((data) => {
      if (!controller.signal.aborted) setItem(data);
    }).catch((problem: unknown) => {
      if (controller.signal.aborted) return;
      onError(problem);
      setError(problem instanceof Error ? problem.message : "无法读取证据。");
    });
    return () => controller.abort();
  }, [taskId, id, onError]);
  return { item: taskId === demo.taskId ? demoResearch.evidence.find((entry) => entry.evidence_id === id) : item, error };
}

function LegacyCard({ taskId, evidenceId, agentId, reportId, known, onClick, onError }: {
  taskId: string; evidenceId: string; agentId: string; reportId?: string; known: boolean;
  onClick: () => void; onError: (problem: unknown) => void;
}) {
  const { item, error } = useLegacyEvidence(taskId, evidenceId, onError);
  return <SourceCard title={item?.source_title ?? (error ? "证据暂不可读" : "正在读取证据…")} category={item?.source_type}
    publisher={hostOf(item?.source_url)} agent={agentId} onClick={onClick}>
    <span className="source-badge">证据已入库</span><span className="source-badge">无质量数据</span><CitationBadge reportId={reportId} known={known} />
  </SourceCard>;
}

function LegacyDetail({ taskId, evidenceId, agentId, reportId, known, onError }: {
  taskId: string; evidenceId: string; agentId?: string; reportId?: string; known: boolean; onError: (problem: unknown) => void;
}) {
  const { item, error } = useLegacyEvidence(taskId, evidenceId, onError);
  if (error) return <p className="source-empty error-text" role="alert">{error}</p>;
  if (!item) return <p className="source-empty muted" role="status">正在读取证据…</p>;
  return <article className="source-detail">
    <p className="eyebrow">{sourceTypes[item.source_type]}{hostOf(item.source_url) && ` · ${hostOf(item.source_url)}`}</p>
    <h3>{item.source_title}</h3>
    {agentId && <ExpertLabel id={agentId} avatar />}
    <div className="source-badges"><span className="source-badge">证据已入库</span>
      <span className="source-badge">无质量数据</span><CitationBadge reportId={reportId} known={known} />
    </div>
    <div className="source-links"><OriginalLink url={publicSourceUrl(item.source_url)} />
      {reportId && <ReportCitation taskId={taskId} reportId={reportId} evidenceId={evidenceId} />}
    </div>
    <h4>证据摘录</h4><blockquote className="source-body">{item.excerpt}</blockquote>
    {item.source_locator && <p className="muted">{item.source_locator}</p>}
  </article>;
}

function OriginalLink({ url }: { url?: string }) {
  return url ? <a className="text-link" href={url} target="_blank" rel="noreferrer">打开原始来源 <ArrowUpRight size={15} aria-hidden /></a> : null;
}

function ReportCitation({ taskId, reportId, evidenceId }: { taskId: string; reportId: string; evidenceId: string }) {
  return <Link className="text-link" href={`${reportHref(taskId, reportId)}#evidence-${encodeURIComponent(evidenceId)}`}>
    查看报告引用 <ArrowUpRight size={15} aria-hidden />
  </Link>;
}
