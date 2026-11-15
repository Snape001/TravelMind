import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft, BookOpenText, Check, Wrench } from "lucide-react";
import { ExpertPortrait } from "@/components/expert-identity";
import { experts, findExpert, tiers } from "@/lib/experts";

type Props = { params: Promise<{ agentId: string }> };

export function generateStaticParams() {
  return experts.map(({ id }) => ({ agentId: id }));
}

export async function generateMetadata({ params }: Props) {
  const expert = findExpert((await params).agentId);
  return { title: expert ? `${expert.name ?? expert.title} · 专家公会 · TravelMind` : "专家公会 · TravelMind" };
}

export default async function ExpertProfilePage({ params }: Props) {
  const { agentId } = await params;
  const expert = findExpert(agentId);
  if (!expert) notFound();
  const tier = tiers.find(({ id }) => id === expert.tier);
  return (
    <main className="shell inner-page guild-page profile-page">
      <Link className="text-link back" href="/guild"><ArrowLeft size={16} aria-hidden /> 专家公会</Link>
      <article className="panel expert-profile">
        <header className="profile-header">
          <ExpertPortrait expert={expert} />
          <div className="profile-intro">
            <p className="profile-tier">{tier?.title}</p>
            <h1>{expert.name ?? expert.title}</h1>
            {expert.name && <p className="expert-role">{expert.title}{expert.honorific && ` · ${expert.honorific}`}</p>}
            <p className="profile-description">{expert.description}</p>
          </div>
        </header>
        <div className="profile-sections">
          <section aria-labelledby="profile-fields">
            <h2 id="profile-fields"><BookOpenText size={20} aria-hidden />专业领域</h2>
            <div className="tag-list">{expert.fields.map((field) => <span className="chip" key={field}>{field}</span>)}</div>
            {!!expert.skills?.length && <>
              <h3 className="profile-skills-heading">技能标签</h3>
              <div className="tag-list">{expert.skills.map((skill) => <span className="chip" key={skill}>{skill}</span>)}</div>
            </>}
          </section>
          <section aria-labelledby="profile-tools">
            <h2 id="profile-tools"><Wrench size={20} aria-hidden />工具能力与资料来源</h2>
            <ul>{expert.tools.map((tool) => <li key={tool}><Check size={16} aria-hidden />{tool}</li>)}</ul>
          </section>
        </div>
      </article>
    </main>
  );
}
