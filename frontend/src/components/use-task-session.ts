"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, readTask, subscribe, subscribeResearch } from "@/lib/task-client";
import { isTerminal, nextSnapshot } from "@/lib/task-lifecycle";
import type { AgentEvent, TaskRead } from "@/lib/research";
import type { ResearchEvent } from "@/lib/research-browser";

export type Connection = "connecting" | "open" | "reconnecting" | "closed" | "unavailable";

export function useTaskSession(taskId: string | null, onAccessError: (error: unknown) => void) {
  const [task, setTask] = useState<TaskRead | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [research, setResearch] = useState<ResearchEvent[]>([]);
  const [researchConnection, setResearchConnection] = useState<Connection>("connecting");
  const [error, setError] = useState("");
  const reload = useRef<(() => void) | null>(null);

  const accept = useCallback((incoming: TaskRead) => {
    setTask((previous) => previous ? nextSnapshot(previous, incoming) : incoming);
  }, []);

  useEffect(() => {
    if (taskId === null) return;
    const id = taskId;
    let disposed = false;
    let pending: AbortController | undefined;
    let close: (() => void) | undefined;
    let closeResearch: (() => void) | undefined;
    let ended = false;
    let refreshAgain = false;

    function refresh() {
      if (disposed) return;
      if (pending) {
        refreshAgain = true;
        return;
      }
      const controller = new AbortController();
      pending = controller;
      readTask(id, controller.signal).then((record) => {
        if (controller.signal.aborted) return;
        accept(record);
        setError("");
        closeResearch ??= subscribeResearch(record, (event) => setResearch((current) => [...current, event]),
          setResearchConnection, (problem) => {
            onAccessError(problem);
            refresh();
          });
        if (!close && !ended) {
          close = subscribe(record, 0, (event) => {
            setEvents((current) => [...current, event]);
            const payload = event.payload;
            if (payload.type === "task.status" && isTerminal(payload.status)) {
              ended = true;
              close?.();
              setConnection("closed");
            }
            if (payload.type === "task.clarification" || payload.type === "task.status") refresh();
          }, (state) => {
            setConnection(state);
            refresh();
          });
        }
      }).catch((problem: unknown) => {
        if (controller.signal.aborted) return;
        onAccessError(problem);
        if (problem instanceof ApiError && [401, 403, 404].includes(problem.status)) {
          close?.();
          closeResearch?.();
          refreshAgain = false;
          setTask(null);
          setEvents([]);
          setResearch([]);
          ended = true;
          setConnection("unavailable");
        }
        setError(problem instanceof Error ? problem.message : "无法读取任务状态。");
      }).finally(() => {
        pending = undefined;
        if (refreshAgain) {
          refreshAgain = false;
          refresh();
        }
      });
    }

    reload.current = refresh;
    refresh();
    return () => {
      disposed = true;
      pending?.abort();
      close?.();
      closeResearch?.();
      reload.current = null;
    };
  }, [taskId, accept, onAccessError]);

  return { task, events, connection, research, researchConnection, error, accept, refresh: () => reload.current?.() };
}
