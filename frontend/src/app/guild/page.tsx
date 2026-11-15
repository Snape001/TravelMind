import Link from "next/link";
import { ArrowRight, ArrowUpRight, Compass, FilePenLine, UsersRound } from "lucide-react";
import { ExpertPortrait } from "@/components/expert-identity";
import { guildExperts, tiers } from "@/lib/experts";

export const metadata = { title: "专家公会 · TravelMind" };

const tierIcons = { leadership: Compass, council: UsersRound, editorial: FilePenLine };

export default function Guild() {
  return (
    <main className="shell inner-page guild-page">
      <header className="guild-banner">
        <div className="guild-banner-content">
          <h1>专家公会</h1>
          <p>从研究设计、专题取证到独立审议，查看 TravelMind 的专家分工。</p>
          <nav aria-label="公会分组">
            {tiers.map(({ id, title }) => <a href={`#${id}`} key={id}>{title}<ArrowRight size={14} aria-hidden /></a>)}
          </nav>
        </div>
      </header>
      {tiers.map(({ id, title, description }) => {
        const Icon = tierIcons[id];
        return <section className={`guild-section guild-${id}`} key={id} aria-labelledby={id}>
          <header className="guild-section-heading">
            <span className="icon-tile"><Icon size={20} aria-hidden /></span>
            <div><h2 id={id}>{title}</h2><p>{description}</p></div>
          </header>
          <ul className="guild-grid">
            {guildExperts.filter((expert) => expert.tier === id).map((expert) => <li key={expert.id}>
              <Link className="panel expert-card" href={`/guild/${expert.id}`}>
                <ExpertPortrait expert={expert} />
                <div className="expert-card-copy">
                  <div className="expert-card-heading">
                    <div>{expert.name && <p className="expert-role">{expert.title}</p>}<h3>{expert.name ?? expert.title}</h3></div>
                    <span className="expert-card-arrow"><ArrowUpRight size={18} aria-hidden /></span>
                  </div>
                  <p className="expert-summary">{expert.description}</p>
                  <div className="tag-list">{expert.fields.map((field) => <span className="chip" key={field}>{field}</span>)}</div>
                </div>
              </Link>
            </li>)}
          </ul>
        </section>;
      })}
    </main>
  );
}
