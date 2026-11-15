import Image from "next/image";
import Link from "next/link";
import { expertName, expertTitle, findExpert, type ExpertProfile } from "@/lib/experts";
import { ExpertIcon } from "./expert-icon";

export function ExpertPortrait({ expert }: {
  expert: Pick<ExpertProfile, "id"> & Partial<Pick<ExpertProfile, "portrait">>;
}) {
  return <span className="expert-portrait">
    {expert.portrait ? <Image src={expert.portrait} fill unoptimized alt="" /> : <ExpertIcon id={expert.id} />}
  </span>;
}

export function ExpertAvatar({ id }: { id: string }) {
  const profile = findExpert(id);
  return <span className="expert-avatar">
    {profile?.portrait ? <Image src={profile.portrait} width={34} height={34} unoptimized alt="" /> : <ExpertIcon id={id} />}
  </span>;
}

export function ExpertLabel({ id, avatar = false }: { id: string; avatar?: boolean }) {
  const profile = findExpert(id);
  const name = expertName(id);
  const title = expertTitle(id);
  const contents = <>
    {avatar && <ExpertAvatar id={id} />}
    <span className="expert-identity-copy"><strong>{name}</strong>{title && <small>{title}</small>}</span>
  </>;

  return profile ?
    <Link className="expert-identity" href={`/guild/${profile.id}`}>{contents}</Link> :
    <span className="expert-identity">{contents}</span>;
}
