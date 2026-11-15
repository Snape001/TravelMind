"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, MapPin } from "lucide-react";
import { createDestinationTask, createTask, loginHref, missingEndpoint } from "@/lib/task-client";
import { rememberTask } from "@/lib/task-history";
import { prepareBrief, workbenchHref, type CurrentUser } from "@/lib/research";
import { prepareDestination } from "@/lib/questionnaire";
import { useSession } from "./session";

export function ResearchForm() {
  const { session, refresh, handleError } = useSession();
  return <ResearchFormFields key={session.user?.user_id ?? "guest"} user={session.user}
    status={session.status} refresh={refresh} handleError={handleError} />;
}

function ResearchFormFields({ user, status, refresh, handleError }: {
  user?: CurrentUser;
  status: ReturnType<typeof useSession>["session"]["status"];
  refresh: () => void;
  handleError: (error: unknown) => void;
}) {
  const router = useRouter();
  const [destination, setDestination] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const pending = useRef<AbortController | null>(null);
  const lastRequest = useRef<{ destination: string; key: string } | null>(null);

  useEffect(() => () => pending.current?.abort(), []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending.current || !user) return;
    setError("");
    const controller = new AbortController();
    pending.current = controller;
    try {
      const body = prepareDestination(destination);
      if (lastRequest.current?.destination !== body.destination) {
        lastRequest.current = { destination: body.destination, key: crypto.randomUUID() };
      }
      setSending(true);
      const { key } = lastRequest.current;
      const task = await createDestinationTask(body, key, user.csrf_token, controller.signal).catch((problem) => {
        if (!missingEndpoint(problem)) throw problem;
        return createTask(prepareBrief(body.destination, "", "", "deep"), key, user.csrf_token, controller.signal);
      });
      if (controller.signal.aborted) return;
      rememberTask(user.user_id, task.task_id);
      router.push(workbenchHref(task.task_id));
    } catch (problem) {
      if (controller.signal.aborted) return;
      handleError(problem);
      pending.current = null;
      setSending(false);
      setError(problem instanceof Error ? problem.message : "提交失败，请重试。");
    }
  }

  return <form className="destination-form" onSubmit={submit} aria-label="新建调研">
    <div className="destination-entry">
      <label>
        <MapPin size={22} aria-hidden />
        <input name="destination" aria-label="调研目的地" placeholder="输入目的地，如泉州开元寺" value={destination}
          onChange={(event) => setDestination(event.target.value)} disabled={sending} required />
      </label>
      {status === "anonymous" ? <a className="button primary" href={loginHref}>登录后开始 <ArrowRight size={18} aria-hidden /></a> :
        status === "unavailable" ? <button type="button" className="button primary" onClick={refresh}>重试登录检查</button> :
          <button className="button primary" disabled={sending || !user} type="submit">
            {sending ? "正在创建…" : "开始调研"} <ArrowRight size={18} aria-hidden />
          </button>}
    </div>
    {error && <p className="destination-error" role="alert">{error}</p>}
  </form>;
}
