"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useRef } from "react";
import { ChevronDown, Compass, FileText, FlaskConical, House, LogIn, UserRound, UsersRound } from "lucide-react";
import { AccountAvatar } from "./account-avatar";
import { AccountSignOut } from "./account-sign-out";
import { LoginLink, useSession } from "./session";

const links = [
  { href: "/", match: "/", label: "首页", icon: House },
  { href: "/workbench", match: "/workbench", label: "调研工作台", icon: FlaskConical },
  { href: "/guild", match: "/guild", label: "专家公会", icon: UsersRound },
  { href: "/reports", match: "/reports", label: "报告中心", icon: FileText },
];

export function SiteHeader() {
  const pathname = usePathname();
  const { session, notice } = useSession();
  return <>
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
          {session.user ? <AccountMenu key={session.user.user_id} displayName={session.user.display_name} /> :
            session.status === "loading" ? <span className="header-session-loading" role="status">正在检查登录状态…</span> :
              <div className="header-entry">
                <LoginLink className="button header-login"><LogIn size={15} aria-hidden />登录</LoginLink>
                <LoginLink className="button header-register" register>注册</LoginLink>
              </div>}
        </div>
      </div>
    </header>
    {notice && <p className="session-notice" role="status">{notice}</p>}
  </>;
}

function AccountMenu({ displayName }: { displayName?: string | null }) {
  const menu = useRef<HTMLDetailsElement>(null);
  const trigger = useRef<HTMLElement>(null);
  const name = displayName?.trim() ?? "";

  function close() {
    if (menu.current) menu.current.open = false;
  }

  return <details ref={menu} className="account-menu"
    onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) close(); }}
    onKeyDown={(event) => {
      if (event.key === "Escape" && menu.current?.open) {
        close();
        trigger.current?.focus();
      }
    }}>
    <summary ref={trigger} className="account-trigger" aria-label={name ? `${name}的账户菜单` : "账户菜单"} title={name}>
      <AccountAvatar name={name} />
      <span className="account-label">{name || "我的账户"}</span>
      <ChevronDown size={15} aria-hidden />
    </summary>
    <div className="account-panel">
      <Link href="/account" onClick={close}><UserRound size={16} aria-hidden />个人资料</Link>
      <Link href="/reports" onClick={close}><FileText size={16} aria-hidden />我的报告</Link>
      <AccountSignOut />
    </div>
  </details>;
}
