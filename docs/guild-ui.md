# 专家公会

`/guild` 按领导层、专业专家层和执行层展示九位专家。卡片进入 `/guild/[agentId]`。

## 档案与肖像

- [`experts.ts`](../frontend/src/lib/experts.ts) 提供公会、工作台、事件和报告共用的档案。`writer` 事件与 `report_writer` 档案对应，`planner` 使用研究规划角色的展示身份。文献与互联网研究档案用于展示，实际派发以 Runtime 名册为准。
- [肖像目录](../frontend/public/images/experts/) 保留九张生成的 640 × 640 WebP，文件名对应档案 ID。历史旅游规划档案仍可访问。旅游规划和研究规划使用图标，未知 Agent 显示原始 ID。

## 工作台与报告

工作台用 AgentEvent v1 展示人员、分工及工具事件，点击组织图节点可按 Agent ID 筛选事件。报告按 `author_agent_ids` 展示署名。显式标记共识或争议的章节应用对应样式。泉州只读示例第 2 版可预览这两类章节。
