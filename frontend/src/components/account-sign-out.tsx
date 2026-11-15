"use client";

import { useState } from "react";
import { LogOut } from "lucide-react";
import { useSession } from "./session";

export function AccountSignOut({ className }: { className?: string }) {
  const { signOut, signingOut } = useSession();
  const [error, setError] = useState("");

  async function leave() {
    if (signingOut) return;
    setError("");
    try { await signOut(); }
    catch (problem) { setError(problem instanceof Error ? problem.message : "退出登录失败，请重试。"); }
  }

  return <>
    <button type="button" onClick={leave} aria-disabled={signingOut} className={className}>
      <LogOut size={16} aria-hidden />{signingOut ? "正在退出…" : "退出登录"}
    </button>
    {error && <p className="error-text account-error" role="alert">{error}</p>}
  </>;
}
