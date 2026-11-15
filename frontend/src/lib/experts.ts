export const tiers = [
  { id: "leadership", title: "领导层", description: "确定研究方向与专家分工" },
  { id: "council", title: "专业专家层", description: "按专题研究、核对证据与交叉质询" },
  { id: "editorial", title: "执行层", description: "整合研究成果、撰写报告并独立审核" },
] as const;

type ExpertRole = {
  id: string;
  title: string;
  tier: (typeof tiers)[number]["id"];
  description: string;
  fields: readonly string[];
  tools: readonly string[];
};

export type ExpertProfile = ExpertRole & {
  name: string;
  portrait: `/images/experts/${string}.webp`;
};

type ExpertIdentity = ExpertRole & Partial<Pick<ExpertProfile, "name" | "portrait">>;

// Display fixture. All keys match the agent IDs used by tasks, events and reports.
const profiles = {
  research_director: {
    name: "程策远", title: "首席研究总监", tier: "leadership",
    description: "明确调研目标和边界，识别信息缺口，组建跨领域团队。研究过程中协调任务依赖，并决定哪些结论需要补充核验。",
    fields: ["需求澄清", "研究设计", "专家调度"],
    tools: ["调研计划编排", "任务依赖与进度协调"],
  },
  history_culture: {
    name: "沈观澜", title: "历史文化专家", tier: "council",
    description: "从地方史、文化遗产和民俗切入，辨析历史叙述的依据，梳理遗产与当代城市生活的关系。",
    fields: ["地方史", "文化遗产", "民俗"],
    tools: ["官方遗产档案", "地方志检索", "历史资料交叉核验"],
  },
  natural_geography: {
    name: "顾山河", title: "自然地理专家", tier: "council",
    description: "结合地形、生态与气候条件，研判景观的季节差异和户外游览限制。",
    fields: ["地形地貌", "生态环境", "气候"],
    tools: ["地图与地理资料", "气象公告", "生态保护资料"],
  },
  tourism_resources: {
    name: "陆知行", title: "旅游资源专家", tier: "council",
    description: "梳理景区与公共文化空间，核对开放、预约及可达条件，帮助读者比较游览选择。",
    fields: ["景点分析", "游览体验", "服务设施"],
    tools: ["景区与场馆公告", "公共服务网页检索"],
  },
  social_sentiment: {
    name: "苏听雨", title: "社交舆情专家", tier: "council",
    description: "从公开游客反馈中识别反复出现的体验和争议，标注样本所处的季节和出行场景。",
    fields: ["游客体验", "公开评论", "观点分歧"],
    tools: ["公开评价检索", "游客反馈归类"],
  },
  literature_research: {
    name: "温文渊", title: "文献研究专家", tier: "council",
    description: "追索论文、地方志与开放图书，核对文献版本及原始出处，补足关键结论的研究背景。",
    fields: ["学术文献", "地方志", "图书资料"],
    tools: ["OpenAlex 文献检索", "开放书目", "引文追溯"],
  },
  internet_research: {
    name: "谢行舟", title: "互联网研究专家", tier: "council",
    description: "检索政策、公告和新闻，核对发布主体及时间，追踪仍在变化的公共信息。",
    fields: ["官方信息", "政策公告", "新闻核验"],
    tools: ["博查网页检索", "Firecrawl 正文提取", "来源与时效核验"],
  },
  report_writer: {
    name: "叶知秋", title: "首席执笔官", tier: "editorial",
    description: "统筹报告结构，整合专家结论与分歧，为关键判断保留署名、引用和适用条件。",
    fields: ["报告写作", "证据整合", "章节结构"],
    tools: ["专家研究成果", "章节署名与证据引用"],
  },
  reviewer: {
    name: "柳怀真", title: "监察审议官", tier: "editorial",
    description: "独立核对事实、来源及覆盖范围，指出证据薄弱的判断，并提出具体修订意见。",
    fields: ["事实核验", "证据审核", "一致性"],
    tools: ["报告审阅", "引用与原始证据对照"],
  },
} satisfies Record<string, Omit<ExpertProfile, "id" | "portrait">>;

export const guildExperts: ExpertProfile[] = Object.entries(profiles).map(([id, profile]) => ({
  ...profile, id, portrait: `/images/experts/${id}.webp`,
}));

// This runtime role remains addressable by existing tasks and bookmarks.
const travelPlanning: ExpertRole = {
  id: "travel_planning", title: "旅游规划专家", tier: "council",
  description: "结合行程天数、交通方式与预算，比较可执行的游览安排。",
  fields: ["交通", "住宿", "行程与消费"],
  tools: ["地图资料", "交通公告"],
};

export const experts: readonly ExpertIdentity[] = [...guildExperts, travelPlanning];
const byId = new Map(experts.map((expert) => [expert.id, expert]));

export const findExpert = (id: string) => byId.get(id);
export function expertName(id: string) {
  const expert = findExpert(id);
  return expert?.name ?? expert?.title ?? id;
}

export function expertTitle(id: string) {
  const expert = findExpert(id);
  return expert?.name ? expert.title : undefined;
}
