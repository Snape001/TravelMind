"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { Compass, FileText, FlaskConical, House, UsersRound } from "lucide-react";
import { loginHref } from "@/lib/task-client";
import { useSession } from "./session";

const links = [
  { href: "/", match: "/", label: "首页", icon: House },
  { href: "/workbench", match: "/workbench", label: "调研工作台", icon: FlaskConical },
  { href: "/guild", match: "/guild", label: "专家公会", icon: UsersRound },
  { href: "/reports", match: "/reports", label: "报告中心", icon: FileText },
];

export function SiteHeader() {
  const pathname = usePathname();
  const { session, signOut } = useSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function leave() {
    setBusy(true);
    setError("");
    try { await signOut(); }
    catch (problem) { setError(problem instanceof Error ? problem.message : "退出登录失败。"); }
    finally { setBusy(false); }
  }
  return (
    <header className="site-header">
      <div className="shell header-grid">
        <Link className="wordmark" href="/">
          <span className="brand-icon"><Compass size={23} strokeWidth={1.7} /></span>
          <span>Travel<span className="brand-accent">Mind</span></span>
        </Link>
        <nav className="site-nav" aria-label="主导航">
          {links.map(({ href, match, label, icon: Icon }) => {
            const current = pathname === match || pathname.startsWith(`${match}/`);
            return <Link key={href} href={href} aria-current={current ? "page" : undefined}>
              <Icon size={16} aria-hidden />{label}
            </Link>;
          })}
        </nav>
        <div className="header-session">
          {session.user ? <>
            <span className="account-name" title={session.user.display_name ?? "已登录"}>{session.user.display_name ?? "已登录"}</span>
            <button className="button secondary" type="button" onClick={leave} disabled={busy}>{busy ? "正在退出…" : "退出"}</button>
          </> : session.status === "anonymous" && <a className="button secondary" href={loginHref}>登录</a>}
          {error && <p className="error-text header-session-error" role="alert">{error}</p>}
        </div>
      </div>
    </header>
  );
}
