import { UserRound } from "lucide-react";

export function AccountAvatar({ name }: { name: string }) {
  return <span className="account-avatar" aria-hidden>{Array.from(name)[0] || <UserRound size="54%" />}</span>;
}
