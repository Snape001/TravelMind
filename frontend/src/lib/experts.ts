export const tiers = [
  { id: "leadership", title: "领导层", description: "理解需求与组织调研" },
  { id: "council", title: "决策层", description: "按专题并行研究" },
  { id: "editorial", title: "执行层", description: "撰写报告与独立审核" },
] as const;

type Expert = {
  id: string;
  title: string;
  english: string;
  tier: typeof tiers[number]["id"];
  description: string;
  fields: string[];
  tools: string[];
};

export const experts: Expert[] = [
  { id: "research_director", title: "调研总监", english: "Research Director", tier: "leadership", description: "明确调研目标，识别信息缺口，组建专家团队并分配任务。", fields: ["需求澄清", "任务规划", "专家调度"], tools: ["调研计划", "进度协调"] },
  { id: "history_culture", title: "历史文化专家", english: "History & Culture", tier: "council", description: "从地方史、文化遗产和民俗出发，解读目的地的人文背景。", fields: ["地方史", "文化遗产", "民俗"], tools: ["官方资料", "地方志"] },
  { id: "natural_geography", title: "自然地理专家", english: "Natural Geography", tier: "council", description: "分析地形、生态与季节条件，评估户外游览所需的准备。", fields: ["地理环境", "生态", "气候"], tools: ["地图资料", "气象公告"] },
  { id: "tourism_resources", title: "旅游资源专家", english: "Tourism Resources", tier: "council", description: "梳理可游览的景点与文化空间，核对预约和游览条件。", fields: ["景点分析", "游览体验", "预约方式"], tools: ["景区公告", "网页检索"] },
  { id: "social_sentiment", title: "社交舆情专家", english: "Social Sentiment", tier: "council", description: "归纳公开的游客反馈，识别反复出现的体验与争议。", fields: ["游客体验", "公开评论", "观点分歧"], tools: ["公开评价", "舆情分析"] },
  { id: "literature_research", title: "文献研究专家", english: "Literature Research", tier: "council", description: "查找论文、地方志及开放图书，并记录可追溯的出处。", fields: ["学术文献", "地方志", "图书资料"], tools: ["OpenAlex", "开放书目"] },
  { id: "internet_research", title: "互联网研究专家", english: "Web Research", tier: "council", description: "检索官方网页、公告和新闻，对照发布时间并核验资料来源。", fields: ["官方信息", "政策公告", "新闻"], tools: ["博查", "Firecrawl"] },
  { id: "travel_planning", title: "旅游规划专家", english: "Travel Planning", tier: "council", description: "结合行程天数、交通方式与预算，比较可执行的游览安排。", fields: ["交通", "住宿", "行程与消费"], tools: ["地图资料", "交通公告"] },
  { id: "report_writer", title: "首席撰写师", english: "Lead Editor", tier: "editorial", description: "整合专家结论，组织报告章节并标注引用与署名。", fields: ["报告写作", "证据整合", "章节结构"], tools: ["研究成果", "证据引用"] },
  { id: "reviewer", title: "独立审核员", english: "Independent Reviewer", tier: "editorial", description: "独立核对事实、来源与覆盖范围，提出可执行的修订意见。", fields: ["事实核验", "证据审核", "一致性"], tools: ["报告审阅", "引用校对"] },
];

export const findExpert = (id: string) => experts.find((expert) => expert.id === id);
export const expertName = (id: string) => findExpert(id)?.title ?? id;
