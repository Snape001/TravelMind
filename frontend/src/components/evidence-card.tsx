"use client";

import { useEffect, useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { readEvidence } from "@/lib/task-client";
import { formatDate, type EvidenceRead } from "@/lib/research";

const sourceTypes: Record<EvidenceRead["source_type"], string> = {
  web: "网页", official: "官方资料", academic: "学术资料", book: "图书", social: "社交媒体", map: "地图",
};

export function EvidenceCard({ taskId, evidenceId }: { taskId: string; evidenceId: string }) {
  const [evidence, setEvidence] = useState<EvidenceRead | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    readEvidence(taskId, evidenceId, controller.signal).then((item) => {
      if (!controller.signal.aborted) setEvidence(item);
    }).catch((problem: Error) => {
      if (!controller.signal.aborted) setError(problem.message);
    });
    return () => controller.abort();
  }, [taskId, evidenceId]);

  if (error) return <p className="error-text" role="alert">{error}</p>;
  if (!evidence) return <p className="muted" role="status">正在读取证据…</p>;
  return (
    <article className="evidence-card">
      <p className="eyebrow">{sourceTypes[evidence.source_type]} · 采集于 {formatDate(evidence.retrieved_at)}</p>
      <h3>{evidence.source_title}</h3>
      <blockquote>{evidence.excerpt}</blockquote>
      {evidence.source_locator && <p className="muted">{evidence.source_locator}</p>}
      {evidence.published_at && <p className="muted">发布于 {formatDate(evidence.published_at)}</p>}
      {evidence.source_url && /^https?:\/\//i.test(evidence.source_url) &&
        <a className="text-link" href={evidence.source_url} target="_blank" rel="noreferrer">打开原始来源 <ArrowUpRight size={15} aria-hidden /></a>}
    </article>
  );
}
