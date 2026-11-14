import { BookOpenText, Compass, FilePenLine, Globe, Landmark, MapPinned, MessagesSquare, Mountain, ScanSearch, ShieldCheck, UserRound } from "lucide-react";

const icons: Record<string, typeof Compass> = {
  research_director: Compass, history_culture: Landmark, natural_geography: Mountain, tourism_resources: ScanSearch,
  social_sentiment: MessagesSquare, literature_research: BookOpenText, internet_research: Globe,
  travel_planning: MapPinned, report_writer: FilePenLine, reviewer: ShieldCheck,
};

export function ExpertIcon({ id }: { id: string }) {
  const Icon = Object.hasOwn(icons, id) ? icons[id] : UserRound;
  return <span className={`icon-tile agent-icon agent-${id}`}><Icon size={20} aria-hidden /></span>;
}
