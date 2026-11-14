"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { appendEvent } from "@/lib/agent-events";
import { ApiError, readTask, subscribe } from "@/lib/task-client";
import { isTerminal, nextSnapshot } from "@/lib/task-lifecycle";
import type { AgentEvent, TaskRead } from "@/lib/research";

export type Connection = "connecting" | "open" | "reconnecting" | "closed" | "unavailable";

export function useTaskSession(taskId: string, onAccessError: (error: unknown) => void) {
  const [task, setTask] = useState<TaskRead | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [error, setError] = useState("");
  const reload = useRef<(() => void) | null>(null);

  const accept = useCallback((incoming: TaskRead) => {
    setTask((previous) => previous ? nextSnapshot(previous, incoming) : incoming);
  }, []);

  useEffect(() => {
    let disposed = false;
    let pending: AbortController | undefined;
    let close: (() => void) | undefined;
    let accepted: AgentEvent[] = [];
    let ended = false;
    let started = false;

    function refresh() {
      if (disposed) return;
      pending?.abort();
      const controller = new AbortController();
      pending = controller;
      readTask(taskId, controller.signal).then((record) => {
        if (controller.signal.aborted) return;
        accept(record);
        setError("");
        if (!started) {
          started = true;
          close = subscribe(record.task_id, 0, (event) => {
            const next = appendEvent(accepted, event, record);
            if (next === accepted) return false;
            accepted = next;
            setEvents(next);
            const payload = event.payload;
            if (payload.type === "task.status") {
              if (isTerminal(payload.status)) {
                ended = true;
                close?.();
                setConnection("closed");
              }
            }
            if (payload.type === "task.clarification" || payload.type === "task.status") refresh();
            return true;
          }, (state) => {
            if (ended) return;
            setConnection(state);
            refresh();
          });
        }
      }).catch((problem: unknown) => {
        if (controller.signal.aborted) return;
        onAccessError(problem);
        if (problem instanceof ApiError && [401, 403, 404].includes(problem.status) && problem.code !== "CSRF_INVALID") {
          close?.();
          setTask(null);
          setEvents([]);
          ended = true;
          setConnection("unavailable");
        }
        setError(problem instanceof Error ? problem.message : "无法读取任务状态。");
      });
    }

    reload.current = refresh;
    refresh();
    return () => {
      disposed = true;
      pending?.abort();
      close?.();
      reload.current = null;
    };
  }, [taskId, accept, onAccessError]);

  return { task, events, connection, error, accept, refresh: () => reload.current?.() };
}
