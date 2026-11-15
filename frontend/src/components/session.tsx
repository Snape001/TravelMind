"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { providerNavigation } from "@/lib/account";
import { ApiError, loginHref, logoutAccount, readSession, registerHref } from "@/lib/task-client";
import { rememberLoginReturn, takeLoginReturn } from "@/lib/login-return";
import type { CurrentUser } from "@/lib/research";

type Session =
  | { status: "loading"; user?: never }
  | { status: "unavailable"; user?: never }
  | { status: "anonymous"; reason?: "expired" | "unverified"; user?: never }
  | { status: "authenticated"; user: CurrentUser };

const changes = "travelmind.session-changed.live";
const sessionCheckInterval = 30_000;
const Context = createContext<{
  session: Session;
  refresh: () => void;
  handleError: (error: unknown) => void;
  signOut: () => Promise<void>;
  signingOut: boolean;
  notice: string;
} | null>(null);

function publishSessionChange() {
  try { localStorage.setItem(changes, crypto.randomUUID()); } catch { /* Other tabs recheck on focus. */ }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [session, setSession] = useState<Session>({ status: "loading" });
  const [signingOut, setSigningOut] = useState(false);
  const [notice, setNotice] = useState("");
  const pending = useRef<AbortController | null>(null);
  const restoringLogin = useRef(true);
  const leaving = useRef(false);
  const refresh = useCallback(() => {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    readSession(controller.signal).then((user) => {
      if (controller.signal.aborted) return;
      if (user.account_management_enabled === true && user.email_verified === false) {
        setSession({ status: "anonymous", reason: "unverified" });
        return;
      }
      // The existing OIDC callback lands here after issuing the session cookie.
      if (restoringLogin.current && window.location.pathname === "/workbench") {
        try {
          const path = takeLoginReturn(window.location.origin, window.sessionStorage);
          if (path) {
            publishSessionChange();
            router.replace(path);
          }
        } catch { /* Browsers without storage can use the callback landing page. */ }
      }
      restoringLogin.current = false;
      setNotice("");
      setSession({ status: "authenticated", user });
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      if (error instanceof ApiError && error.code === "AUTH_EMAIL_UNVERIFIED") {
        setSession({ status: "anonymous", reason: "unverified" });
      } else if (error instanceof ApiError && error.status === 401) {
        setSession((current) => current.status === "anonymous" ? current :
          { status: "anonymous", reason: current.user ? "expired" : undefined });
      } else setSession((current) => current.user ? current : { status: "unavailable" });
    });
  }, [router]);

  const handleError = useCallback((error: unknown) => {
    if (!(error instanceof ApiError)) return;
    if (error.status === 401 || error.code === "UNAUTHENTICATED") {
      pending.current?.abort();
      setSession({ status: "anonymous", reason: "expired" });
    } else if (error.code === "AUTH_EMAIL_UNVERIFIED") {
      pending.current?.abort();
      setSession({ status: "anonymous", reason: "unverified" });
    } else if (error.code === "CSRF_INVALID") refresh();
  }, [refresh]);

  useEffect(() => {
    refresh();
    const onStorage = (event: StorageEvent) => {
      if (event.key !== changes) return;
      setSession({ status: "loading" });
      refresh();
    };
    const onPageShow = (event: PageTransitionEvent) => {
      if (!event.persisted) return;
      setSession({ status: "loading" });
      refresh();
    };
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
    const user = session.user;
    if (!user || leaving.current) return;
    leaving.current = true;
    setSigningOut(true);
    try {
      const providerUrl = await logoutAccount(user);
      pending.current?.abort();
      setSession({ status: "anonymous" });
      publishSessionChange();
      try { takeLoginReturn(window.location.origin, window.sessionStorage); } catch { /* Optional browser storage. */ }

      if (user.account_management_enabled === true) {
        if (providerUrl) {
          try {
            window.location.assign(providerNavigation(providerUrl));
            return;
          } catch { /* The application session has already ended. */ }
        }
        setNotice("已退出 TravelMind，身份服务的退出状态尚未确认。");
      }
    } catch (problem) {
      handleError(problem);
      if (!(problem instanceof ApiError && problem.status === 401)) throw problem;
    } finally {
      leaving.current = false;
      setSigningOut(false);
    }
  }

  return <Context.Provider value={{ session, refresh, handleError, signOut, signingOut, notice }}>{children}</Context.Provider>;
}

export function useSession() {
  const value = useContext(Context);
  if (!value) throw new Error("SessionProvider is missing");
  return value;
}

export function saveLoginReturn() {
  try { rememberLoginReturn(window.location, window.sessionStorage); } catch { /* Optional browser storage. */ }
}

export function LoginLink({ children, className, register = false }: { children: ReactNode; className?: string; register?: boolean }) {
  return <a className={className} href={register ? registerHref : loginHref} onClick={saveLoginReturn}>{children}</a>;
}

export function SessionPrompt({ message = "登录后继续调研或查看自己的任务。" }: { message?: string }) {
  const { session, refresh } = useSession();
  if (session.status === "authenticated") return null;
  if (session.status === "loading") return <p className="muted" role="status">正在检查登录状态…</p>;
  if (session.status === "unavailable") return <div className="session-prompt">
    <p role="alert">暂时无法读取登录状态。</p><button type="button" className="button secondary" onClick={refresh}>重新检查</button>
  </div>;
  return <div className="session-prompt">
    <p role={session.reason ? "status" : undefined}>{session.reason === "unverified" ? "请先完成邮箱验证，再重新登录。" :
      session.reason === "expired" ? "会话已过期，请重新登录。" : message}</p>
    <LoginLink className="button primary">{session.reason ? "重新登录" : "登录"}</LoginLink>
  </div>;
}
