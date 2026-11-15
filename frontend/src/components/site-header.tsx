"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useRef, useState } from "react";
import { ChevronDown, Compass, FileText, FlaskConical, House, LogIn, LogOut, UserRound, UsersRound } from "lucide-react";
import { LoginLink, useSession } from "./session";

const links = [
  { href: "/", match: "/", label: "首页", icon: House },
  { href: "/workbench", match: "/workbench", label: "调研工作台", icon: FlaskConical },
  { href: "/guild", match: "/guild", label: "专家公会", icon: UsersRound },
  { href: "/reports", match: "/reports", label: "报告中心", icon: FileText },
];

export function SiteHeader() {
  const pathname = usePathname();
  const { session, signOut } = useSession();
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
          {session.user ? <AccountMenu key={session.user.user_id} displayName={session.user.display_name} signOut={signOut} /> :
            session.status === "loading" ? <span className="header-session-loading" role="status">正在检查登录状态…</span> :
              <LoginLink className="button secondary header-login"><LogIn size={15} aria-hidden />登录 / 注册</LoginLink>}
        </div>
      </div>
    </header>
  );
}

function AccountMenu({ displayName, signOut }: { displayName?: string | null; signOut: () => Promise<void> }) {
  const menu = useRef<HTMLDetailsElement>(null);
  const trigger = useRef<HTMLElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const name = displayName?.trim();

  function close() {
    if (menu.current) menu.current.open = false;
  }

  async function leave() {
    if (busy) return;
    setBusy(true);
    setError("");
    try { await signOut(); }
    catch (problem) { setError(problem instanceof Error ? problem.message : "退出登录失败，请重试。"); }
    finally { setBusy(false); }
  }

  return <details ref={menu} className="account-menu"
    onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) close(); }}
    onKeyDown={(event) => {
      if (event.key === "Escape" && menu.current?.open) {
        close();
        trigger.current?.focus();
      }
    }}>
    <summary ref={trigger} className="account-trigger" aria-label="账户菜单" title={name}>
      <span className="account-avatar" aria-hidden>{name ? Array.from(name)[0] : <UserRound size={17} />}</span>
      <ChevronDown size={15} aria-hidden />
    </summary>
    <div className="account-panel">
      <p className="account-name">{name || "已登录"}</p>
      <Link href="/reports" onClick={close}><FileText size={16} aria-hidden />我的报告</Link>
      <button type="button" onClick={leave} aria-disabled={busy}>
        <LogOut size={16} aria-hidden />{busy ? "正在退出…" : "退出登录"}
      </button>
      {error && <p className="error-text account-error" role="alert">{error}</p>}
    </div>
  </details>;
}
