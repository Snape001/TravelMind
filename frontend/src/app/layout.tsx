import type { Metadata } from "next";
import type { ReactNode } from "react";
import { SiteHeader } from "@/components/site-header";
import { SessionProvider } from "@/components/session";
import "./globals.css";
import "./guild-ui.css";
import "./desktop.css";
import "./intake.css";
import "./report-map.css";
import "./account.css";

export const metadata: Metadata = {
  title: "TravelMind",
  description: "多智能体旅游深度调研与决策平台",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body><SessionProvider><SiteHeader />{children}</SessionProvider></body>
    </html>
  );
}
