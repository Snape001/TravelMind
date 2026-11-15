"use client";

import { useEffect, useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { demo, demoResearch } from "@/lib/demo";
import { readEvidence } from "@/lib/task-client";
import { formatDate, type EvidenceRead } from "@/lib/research";
import { publicSourceUrl, sourceTypes } from "@/lib/research-browser";

export function EvidenceCard({ taskId, evidenceId }: { taskId: string; evidenceId: string }) {
  const example = taskId === demo.taskId;
  const [loaded, setEvidence] = useState<EvidenceRead | null>(null);
  const [loadError, setError] = useState("");
  const evidence = example ? demoResearch.evidence.find(({ evidence_id }) => evidence_id === evidenceId) : loaded;
  const error = example && !evidence ? "该任务的证据暂不可用。" : loadError;
  useEffect(() => {
    if (example) return;
    const controller = new AbortController();
    readEvidence(taskId, evidenceId, controller.signal).then((item) => {
      if (!controller.signal.aborted) setEvidence(item);
    }).catch((problem: Error) => {
      if (!controller.signal.aborted) setError(problem.message);
    });
    return () => controller.abort();
  }, [taskId, evidenceId, example]);

  if (error) return <p className="error-text" role="alert">{error}</p>;
  if (!evidence) return <p className="muted" role="status">正在读取证据…</p>;
  const url = publicSourceUrl(evidence.source_url);
  return (
    <article className="evidence-card">
      <p className="eyebrow">{sourceTypes[evidence.source_type]} · 采集于 {formatDate(evidence.retrieved_at)}</p>
      <h3>{evidence.source_title}</h3>
      <blockquote>{evidence.excerpt}</blockquote>
      {evidence.source_locator && <p className="muted">{evidence.source_locator}</p>}
      {evidence.published_at && <p className="muted">发布于 {formatDate(evidence.published_at)}</p>}
      {url && <a className="text-link" href={url} target="_blank" rel="noreferrer">打开原始来源 <ArrowUpRight size={15} aria-hidden /></a>}
    </article>
  );
}
