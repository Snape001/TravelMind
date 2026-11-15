export function reportDiscussion(heading: string): "consensus" | "dispute" | "discussion" | undefined {
  const title = heading.trim().replace(/^#+\s*/, "")
    .replace(/^(?:第[一二三四五六七八九十\d]+[章节]\s*|[一二三四五六七八九十\d]+[、.．)）]\s*)/, "")
    .replaceAll(/\s/g, "");
  const consensus = /^(?:专家|团队)?(?:共识|一致意见)(?=$|[：:、与和及]|暨)/.test(title);
  const dispute = /^(?:专家|团队)?(?:观点)?(?:分歧|争议)(?=$|[：:、与和及]|暨)/.test(title);
  if ((consensus && /分歧|争议/.test(title)) || (dispute && /共识|一致意见/.test(title))) return "discussion";
  if (consensus) return "consensus";
  if (dispute) return "dispute";
}
