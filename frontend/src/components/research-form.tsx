"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, MapPin } from "lucide-react";
import { destinationLoginDraft, prepareDestination } from "@/lib/intake";
import { createDestinationTask, loginHref } from "@/lib/task-client";
import { rememberTask } from "@/lib/task-history";
import { workbenchHref } from "@/lib/research";
import { saveLoginReturn, useSession } from "./session";

export function ResearchForm() {
  const { session, handleError, refresh } = useSession();
  const router = useRouter();
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const pending = useRef<AbortController | null>(null);
  const lastRequest = useRef<{ destination: string; key: string; userId: string } | null>(null);
  const userId = session.user?.user_id;

  useEffect(() => {
    try {
      const saved = sessionStorage.getItem(destinationLoginDraft);
      if (input.current && saved) input.current.value = saved;
      sessionStorage.removeItem(destinationLoginDraft);
    } catch { /* A browser that blocks storage can still start research. */ }
  }, []);
  useEffect(() => () => {
    pending.current?.abort();
    pending.current = null;
  }, [userId]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending.current) return;
    setError("");
    const user = session.user;
    let controller: AbortController | undefined;
    try {
      const body = prepareDestination(input.current?.value ?? "");
      if (session.status === "anonymous") {
        try { sessionStorage.setItem(destinationLoginDraft, body.destination); } catch { /* Optional draft. */ }
        saveLoginReturn();
        window.location.assign(loginHref);
        return;
      }
      if (!user) {
        refresh();
        return;
      }
      controller = new AbortController();
      pending.current = controller;
      if (lastRequest.current?.destination !== body.destination || lastRequest.current.userId !== user.user_id) {
        lastRequest.current = { destination: body.destination, key: crypto.randomUUID(), userId: user.user_id };
      }
      setSending(true);
      const task = await createDestinationTask(body, lastRequest.current.key, user.csrf_token, controller.signal);
      if (controller.signal.aborted) return;
      rememberTask(user.user_id, task.task_id);
      router.push(workbenchHref(task.task_id));
    } catch (problem) {
      if (controller?.signal.aborted) return;
      handleError(problem);
      setError(problem instanceof Error ? problem.message : "提交失败，请重试。");
    } finally {
      if (controller && (!pending.current || pending.current === controller)) {
        pending.current = null;
        setSending(false);
      }
    }
  }

  return (
    <form className="destination-form" onSubmit={submit}>
      <div className="destination-entry">
        <label>
          <MapPin size={21} aria-hidden />
          <input ref={input} name="destination" aria-label="调研目的地" placeholder="输入目的地，例如泉州开元寺"
            autoComplete="off" required disabled={sending} />
        </label>
        <button className="button primary" disabled={sending || session.status === "loading"} type="submit">
          {sending ? "正在创建…" : "开始调研"} <ArrowRight size={18} aria-hidden />
        </button>
      </div>
      {session.status === "unavailable" && <p role="status">暂时无法读取登录状态，点击开始调研可重试。</p>}
      {error && <p className="destination-error" role="alert">{error}</p>}
    </form>
  );
}
