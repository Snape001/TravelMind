"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Compass, FileText, FlaskConical, House, UsersRound } from "lucide-react";

const links = [
  { href: "/", match: "/", label: "首页", icon: House },
  { href: "/workbench", match: "/workbench", label: "调研工作台", icon: FlaskConical },
  { href: "/guild", match: "/guild", label: "专家公会", icon: UsersRound },
  { href: "/reports", match: "/reports", label: "报告中心", icon: FileText },
];

export function SiteHeader() {
  const pathname = usePathname();
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
        <span className="mode"><span className="status-dot" />模拟模式</span>
      </div>
    </header>
  );
}
