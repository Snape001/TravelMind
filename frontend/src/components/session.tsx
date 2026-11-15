"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { ApiError, loginHref, logout, readSession } from "@/lib/task-client";
import { rememberLoginReturn, takeLoginReturn } from "@/lib/login-return";
import type { CurrentUser } from "@/lib/research";

type Session =
  | { status: "loading" | "anonymous"; user?: never }
  | { status: "unavailable"; user?: never }
  | { status: "authenticated"; user: CurrentUser };

const changes = "travelmind.session-changed.live";
const sessionCheckInterval = 30_000;
const Context = createContext<{
  session: Session;
  refresh: () => void;
  handleError: (error: unknown) => void;
  signOut: () => Promise<void>;
} | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [session, setSession] = useState<Session>({ status: "loading" });
  const pending = useRef<AbortController | null>(null);
  const refresh = useCallback(() => {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    readSession(controller.signal).then((user) => {
      if (controller.signal.aborted) return;
      // The existing OIDC callback lands here after issuing the session cookie.
      if (window.location.pathname === "/workbench") {
        try {
          const path = takeLoginReturn(window.location.origin, window.sessionStorage);
          if (path) router.replace(path);
        } catch { /* Browsers without storage can use the callback landing page. */ }
      }
      setSession({ status: "authenticated", user });
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      if (error instanceof ApiError && error.status === 401) setSession({ status: "anonymous" });
      else setSession((current) => current.user ? current : { status: "unavailable" });
    });
  }, [router]);

  const handleError = useCallback((error: unknown) => {
    if (!(error instanceof ApiError)) return;
    if (error.status === 401 || error.code === "UNAUTHENTICATED") {
      pending.current?.abort();
      setSession({ status: "anonymous" });
    } else if (error.code === "CSRF_INVALID") refresh();
  }, [refresh]);

  useEffect(() => {
    refresh();
    const onStorage = (event: StorageEvent) => { if (event.key === changes) refresh(); };
    const onPageShow = (event: PageTransitionEvent) => { if (event.persisted) refresh(); };
    window.addEventListener("storage", onStorage);
    window.addEventListener("focus", refresh);
    window.addEventListener("pageshow", onPageShow);
    return () => {
      pending.current?.abort();
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("focus", refresh);
      window.removeEventListener("pageshow", onPageShow);
    };
  }, [refresh]);

  const authenticated = session.status === "authenticated";
  useEffect(() => {
    if (!authenticated) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, sessionCheckInterval);
    return () => window.clearInterval(timer);
  }, [authenticated, refresh]);

  async function signOut() {
    if (!session.user) return;
    try {
      await logout(session.user.csrf_token);
    } catch (error) {
      handleError(error);
      if (!(error instanceof ApiError && error.status === 401)) throw error;
    }
    pending.current?.abort();
    setSession({ status: "anonymous" });
    try { localStorage.setItem(changes, crypto.randomUUID()); } catch { /* Other tabs recheck on focus. */ }
  }

  return <Context.Provider value={{ session, refresh, handleError, signOut }}>{children}</Context.Provider>;
}

export function useSession() {
  const value = useContext(Context);
  if (!value) throw new Error("SessionProvider is missing");
  return value;
}

export function saveLoginReturn() {
  try { rememberLoginReturn(window.location, window.sessionStorage); } catch { /* Optional browser storage. */ }
}

export function LoginLink({ children, className }: { children: ReactNode; className: string }) {
  return <a className={className} href={loginHref} onClick={saveLoginReturn}>{children}</a>;
}

export function SessionPrompt() {
  const { session, refresh } = useSession();
  if (session.status === "authenticated") return null;
  if (session.status === "loading") return <p className="muted" role="status">正在检查登录状态…</p>;
  if (session.status === "unavailable") return <div className="session-prompt">
    <p role="alert">暂时无法读取登录状态。</p><button type="button" className="button secondary" onClick={refresh}>重新检查</button>
  </div>;
  return <div className="session-prompt"><p>登录后继续调研或查看自己的任务。</p>
    <LoginLink className="button primary">登录</LoginLink>
  </div>;
}
