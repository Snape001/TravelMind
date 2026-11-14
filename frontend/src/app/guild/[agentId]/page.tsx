import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { ExpertIcon } from "@/components/expert-icon";
import { experts, findExpert, tiers } from "@/lib/experts";

type Props = { params: Promise<{ agentId: string }> };
export function generateStaticParams() {
  return experts.map(({ id }) => ({ agentId: id }));
}

export default async function ExpertProfile({ params }: Props) {
  const { agentId } = await params;
  const expert = findExpert(agentId);
  if (!expert) notFound();
  const tier = tiers.find(({ id }) => id === expert.tier);
  return (
    <main className="shell inner-page narrow">
      <Link className="text-link back" href="/guild"><ArrowLeft size={16} aria-hidden /> 专家公会</Link>
      <article className="panel profile">
        <ExpertIcon id={expert.id} />
        <p className="eyebrow">{tier?.title} / {expert.english}</p>
        <h1>{expert.title}</h1>
        <p className="profile-description">{expert.description}</p>
        <section><h2>专业领域</h2><div className="tag-list">{expert.fields.map((field) => <span className="chip" key={field}>{field}</span>)}</div></section>
        <section><h2>工具与数据源</h2><ul>{expert.tools.map((tool) => <li key={tool}>{tool}</li>)}</ul></section>
      </article>
    </main>
  );
}
