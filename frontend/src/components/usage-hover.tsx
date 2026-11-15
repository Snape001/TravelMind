"use client";

import { useEffect, useId, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { usageTotals, type AgentUsage } from "@/lib/research-browser";

const metric = ({ count, partial }: { count: number | null; partial: boolean }) =>
  count === null ? "未上报" : `${count.toLocaleString("zh-CN")}${partial ? "（部分统计）" : ""}`;

export function UsageHover({ calls, label, children, className = "", focusable = false }: {
  calls: AgentUsage[];
  label: string;
  children: ReactNode;
  className?: string;
  focusable?: boolean;
}) {
  const id = useId();
  const [placement, setPlacement] = useState<{ left: number; top?: number; bottom?: number }>();
  const totals = usageTotals(calls);
  const models = [...new Set(calls.map(({ model }) => model))];
  const previews = [...new Set(calls.flatMap(({ prompt_preview }) => prompt_preview ? [prompt_preview] : []))];

  function show(anchor: HTMLElement) {
    const rect = anchor.getBoundingClientRect();
    const left = Math.max(12, Math.min(rect.left, window.innerWidth - 328));
    setPlacement(rect.bottom < window.innerHeight / 2 ? { left, top: rect.bottom + 7 } :
      { left, bottom: window.innerHeight - rect.top + 7 });
  }

  useEffect(() => {
    if (!placement) return;
    const hide = () => setPlacement(undefined);
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") hide(); };
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
      window.removeEventListener("keydown", escape);
    };
  }, [placement]);

  return <span className={`usage-anchor ${className}`} tabIndex={focusable ? 0 : undefined}
    aria-label={focusable ? `${label}的模型调用详情` : undefined} aria-describedby={placement ? id : undefined}
    onMouseEnter={(event) => show(event.currentTarget)} onMouseLeave={() => setPlacement(undefined)}
    onFocus={(event) => show(event.currentTarget)} onBlur={() => setPlacement(undefined)}>
    {children}
    {placement && createPortal(<span id={id} role="tooltip" className="usage-tooltip" style={placement}>
      <strong>模型调用 · {label}</strong>
      <span className="usage-metrics">
        <span>模型</span><b>{models.join("、") || "未上报"}</b>
        <span>Input Tokens</span><b>{metric(totals.input)}</b>
        <span>Output Tokens</span><b>{metric(totals.output)}</b>
      </span>
      <span className="usage-prompt">脱敏 Prompt 预览</span>
      {previews.length ? previews.slice(0, 2).map((preview) => <span className="usage-preview" key={preview}>{preview}</span>) : <span>暂无预览</span>}
      {previews.length > 2 && <span>另有 {previews.length - 2} 条调用预览</span>}
    </span>, document.body)}
  </span>;
}
