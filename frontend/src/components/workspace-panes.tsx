"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { GripVertical } from "lucide-react";

const minimum = { left: 208, right: 264 };
const centerMinimum = 312;
const gutters = 24;
type Side = keyof typeof minimum;
type Widths = Record<Side, number>;
const clamp = (width: number, min: number, max: number) => Math.min(Math.max(width, min), max);

export function WorkspacePanes({ left, center, right }: { left: ReactNode; center: ReactNode; right: ReactNode }) {
  const grid = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [preferred, setPreferred] = useState<Partial<Widths>>({});
  const drag = useRef<{ side: Side; pointer: number; start: number; width: number; max: number } | null>(null);
  const available = Math.max(width, minimum.left + minimum.right + centerMinimum + gutters) - centerMinimum - gutters;
  const leftWidth = clamp(preferred.left ?? width * .215, minimum.left, available - minimum.right);
  const rightWidth = clamp(preferred.right ?? width * .30, minimum.right, available - leftWidth);
  const widths = { left: leftWidth, right: rightWidth };

  useEffect(() => {
    const element = grid.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    function move(event: PointerEvent) {
      const active = drag.current;
      if (!active || active.pointer !== event.pointerId) return;
      const { side, start, width, max } = active;
      const delta = side === "left" ? event.clientX - start : start - event.clientX;
      setPreferred((current) => ({ ...current, [side]: clamp(width + delta, minimum[side], max) }));
    }
    function end(event: PointerEvent) {
      if (drag.current?.pointer === event.pointerId) drag.current = null;
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
    };
  }, []);

  function divider(side: Side) {
    const other = side === "left" ? "right" : "left";
    const max = available - widths[other];
    const sign = side === "left" ? 1 : -1;
    const resize = (value: number) => setPreferred((current) => ({ ...current, [side]: clamp(value, minimum[side], max) }));
    return <div className="workspace-divider" role="separator" aria-orientation="vertical" aria-valuemin={minimum[side]}
      aria-valuemax={max} aria-valuenow={widths[side]} aria-controls={`pane-${side}`}
      aria-label={side === "left" ? "调整专家栏宽度" : "调整资料栏宽度"} tabIndex={0}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.currentTarget.focus({ preventScroll: true });
        drag.current = { side, pointer: event.pointerId, start: event.clientX, width: widths[side], max };
      }}
      onKeyDown={(event) => {
        const step = event.shiftKey ? 40 : 12;
        switch (event.key) {
          case "ArrowLeft": resize(widths[side] - sign * step); break;
          case "ArrowRight": resize(widths[side] + sign * step); break;
          case "Home": resize(minimum[side]); break;
          case "End": resize(max); break;
          default: return;
        }
        event.preventDefault();
      }}
      onDoubleClick={() => setPreferred((current) => ({ ...current, [side]: undefined }))}>
      <GripVertical size={14} aria-hidden />
    </div>;
  }

  return <div className="workspace-grid" ref={grid}
    style={width ? { gridTemplateColumns: `${leftWidth}px 12px minmax(${centerMinimum}px, 1fr) 12px ${rightWidth}px` } : undefined}>
    <aside className="workspace-pane" id="pane-left" aria-label="专家公会导航">{left}</aside>
    {divider("left")}
    <div className="workspace-pane">{center}</div>
    {divider("right")}
    <aside className="workspace-pane" id="pane-right" aria-label="Research Browser">{right}</aside>
  </div>;
}
