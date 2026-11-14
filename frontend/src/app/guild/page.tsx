import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import { ExpertIcon } from "@/components/expert-icon";
import { experts, tiers } from "@/lib/experts";

export const metadata = { title: "专家公会 · TravelMind" };

export default function Guild() {
  return (
    <main className="shell inner-page">
      <div className="page-heading"><h1>专家公会</h1><p>了解各领域 Agent 的专长、资料来源和调研职责。</p></div>
      {tiers.map((tier) => (
        <section className="guild-section" key={tier.id} aria-labelledby={tier.id}>
          <div className="section-heading"><h2 id={tier.id}>{tier.title}</h2><p>{tier.description}</p></div>
          <ul className="guild-grid">
            {experts.filter((expert) => expert.tier === tier.id).map((expert) => <li key={expert.id}>
              <Link className="panel expert-card" href={`/guild/${expert.id}`}>
                <div className="expert-card-top"><ExpertIcon id={expert.id} /><ArrowUpRight size={18} aria-hidden /></div>
                <h3>{expert.title}</h3><p>{expert.description}</p>
                <div className="tag-list">{expert.fields.map((field) => <span className="chip" key={field}>{field}</span>)}</div>
              </Link>
            </li>)}
          </ul>
        </section>
      ))}
    </main>
  );
}
