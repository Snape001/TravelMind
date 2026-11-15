"use client";

import { Route, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { RouteMapPanel, useRouteMap } from "./route-map-panel";
import type { PoiSelection } from "./route-map-canvas";

export function WorkbenchRoutePreview({ taskId, reportId }: { taskId: string; reportId?: string }) {
  const [open, setOpen] = useState(false);
  return <div className="workspace-route-preview">
    <span>陆知行</span>
    <button type="button" className="text-link" onClick={() => setOpen(true)}>
      <Route size={15} aria-hidden />路线规划预览
    </button>
    {open && <RoutePreviewDialog taskId={taskId} reportId={reportId} onClose={() => setOpen(false)} />}
  </div>;
}

function RoutePreviewDialog({ taskId, reportId, onClose }: { taskId: string; reportId?: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const { state, retry } = useRouteMap(taskId, reportId);
  const [selection, setSelection] = useState<PoiSelection | null>(null);
  const select = useCallback((id: string) => setSelection({ id }), []);

  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
  }, []);

  return <dialog ref={dialog} className="route-preview-dialog" onClose={onClose} aria-labelledby="route-preview-title">
    <header className="route-preview-heading">
      <h2 id="route-preview-title">陆知行 · 路线规划结果</h2>
      <button type="button" aria-label="关闭路线预览" onClick={() => dialog.current?.close()}><X size={22} aria-hidden /></button>
    </header>
    <RouteMapPanel state={state} retry={retry} selection={selection} onSelect={select} />
  </dialog>;
}
