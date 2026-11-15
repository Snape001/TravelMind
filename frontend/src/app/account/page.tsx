import { AccountProfile } from "@/components/account-profile";

export const metadata = { title: "个人资料 · TravelMind" };

export default function AccountPage() {
  return <main className="shell narrow inner-page account-page">
    <div className="page-heading"><h1>个人资料</h1></div>
    <AccountProfile />
  </main>;
}
