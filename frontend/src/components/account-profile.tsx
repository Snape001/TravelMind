"use client";

import { useEffect, useRef, useState } from "react";
import { CircleCheck, Pencil, ShieldCheck } from "lucide-react";
import { providerNavigation, type AccountUser } from "@/lib/account";
import { editProfile } from "@/lib/task-client";
import { AccountAvatar } from "./account-avatar";
import { AccountSignOut } from "./account-sign-out";
import { SessionPrompt, useSession } from "./session";

export function AccountProfile() {
  const { session } = useSession();
  if (!session.user) return <SessionPrompt message="登录后可以查看和管理个人资料。" />;
  return <Profile key={session.user.user_id} user={session.user} />;
}

function Profile({ user }: { user: AccountUser }) {
  const { handleError, signingOut } = useSession();
  const pending = useRef<AbortController | null>(null);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState("");
  const name = user.display_name?.trim() ?? "";
  const canEdit = user.account_management_enabled === true;

  useEffect(() => () => pending.current?.abort(), []);

  async function editName() {
    if (pending.current || signingOut) return;
    const controller = new AbortController();
    pending.current = controller;
    setEditing(true);
    setError("");
    try {
      const { authorization_url } = await editProfile(user.csrf_token, controller.signal);
      if (!controller.signal.aborted) window.location.assign(providerNavigation(authorization_url));
    } catch (problem) {
      if (controller.signal.aborted) return;
      handleError(problem);
      setError(problem instanceof Error ? problem.message : "暂时无法打开资料编辑，请重试。");
    } finally {
      pending.current = null;
      if (!controller.signal.aborted) setEditing(false);
    }
  }

  return <div className="account-sections">
    <section className="account-card" aria-labelledby="account-identity">
      <header className="account-card-heading">
        <AccountAvatar name={name} />
        <h2 id="account-identity">账户资料</h2>
      </header>
      <dl className="account-fields">
        <div>
          <dt>昵称</dt><dd>{name || "未设置"}</dd>
          {canEdit && <button className="text-link" type="button" onClick={editName} disabled={editing || signingOut}>
            <Pencil size={15} aria-hidden />{editing ? "正在打开…" : "编辑昵称"}
          </button>}
        </div>
        <div>
          <dt>邮箱</dt><dd>{user.email || "未提供"}</dd>
          {user.email && <span className={user.email_verified === true ? "account-verified" : "muted"}>
            {user.email_verified === true && <CircleCheck size={15} aria-hidden />}
            {user.email_verified === true ? "已验证" : user.email_verified === false ? "待验证" : "验证状态未知"}
          </span>}
        </div>
      </dl>
      {error && <p className="error-text" role="alert">{error}</p>}
    </section>
    <section className="account-card account-security" aria-labelledby="account-security">
      <div>
        <h2 id="account-security"><ShieldCheck size={19} aria-hidden />账户安全</h2>
        <p className="muted">可在登录页通过「忘记密码」重置密码。</p>
      </div>
      <div><AccountSignOut className="button secondary account-exit" /></div>
    </section>
  </div>;
}
