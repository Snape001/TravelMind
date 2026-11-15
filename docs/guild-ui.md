# 专家公会

`/guild` 按领导层、专业专家层和执行层展示档案。卡片进入 `/guild/[agentId]`。

## 档案与肖像

- [`experts.ts`](../frontend/src/lib/experts.ts) 提供公会、工作台、事件和报告共用的档案。`writer` 事件与 `report_writer` 档案对应，`planner` 使用研究规划角色的展示身份。文献与互联网研究档案用于展示，实际派发以 Runtime 名册为准。
- 林景川（山海勘游使）是专业专家层的旅游资源专家，沿用 `tourism_resources`，使用 640 × 640 的 `lin-jingchuan.webp`。技能标签对应现有 `attraction_assessment@1` 的游览资源与现场条件评估。陆知行对应 `travel_planning`，继续使用原肖像 `tourism_resources.webp`。研究规划使用角色图标，未知 Agent 显示原始 ID。 [肖像目录](../frontend/public/images/experts/)

## 工作台与报告

工作台用 AgentEvent v1 展示人员、分工与活动。左栏三级工作流可按专家筛选，并独立显示 Writer 与 Reviewer 的最新运行状态。任务依赖图和分工可展开查看。报告按 `author_agent_ids` 展示署名，合并同一专家的历史 ID 别名。显式标记共识或争议的章节应用对应样式。泉州只读示例第 2 版可预览这两类章节。
