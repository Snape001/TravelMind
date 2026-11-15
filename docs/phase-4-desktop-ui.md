# Phase 4 桌面工作台

首页将目的地提交到 `POST /api/v1/tasks/from-destination`。任务等待补充信息时，从 `intake` 读取当前轮次，按服务端提供的题型、选项和显示条件生成表单。答案使用当前问卷 ID 和幂等键，隐藏题省略。目的地确认必答，研究与出行轮次可以跳过。

历史任务仍支持原有文本澄清、任务控制、报告、证据和 AgentEvent v1。需要登录时，同一浏览器标签页保存目的地草稿，OIDC 回到首页后可继续提交。

## 三栏工作区

- 左栏显示三级工作流和专家状态。Writer 与 Reviewer 依据各自的事件更新，按专家筛选中栏。任务依赖和分工可展开查看。
- 中栏展示对话、状态、来源动作和任务控制。工具标识、耗时和错误码在技术详情中。两侧分隔条支持拖拽、方向键、Home、End 和双击复位。
- 右栏以独立游标消费 `research-events`。抓取状态、分工评分和报告引用跟随该流变化。点击来源读取 `research-sources/{source_id}` 的纯文本正文。列表和正文各自保留滚动区域，可打开经过校验的原始链接。旧任务使用已入库的证据、摘录和可读取的报告引用。
- 专家运行和消息的 Hover 只读取 `agent.usage`。消息按显式事件 ID 关联，未关联的调用只在对应运行显示。重复调用 ID 只计一次。缺失字段显示「未上报」，有数值的部分统计会标明状态，供应商实报的零值原样显示。

## 开发与联调

前端使用当前 FastAPI 的 OpenAPI 生成类型。前端测试复用共享合成 Fixture。运行时请求走同源 Next.js 代理。 [公共接口及类型导出](phase-4-contract.md) · [合成 Fixture](../fixtures/phase4.json)

在 `frontend/` 执行 `npm test`、`npm run lint`、`npm run typecheck` 和 `npm run build`。用提供 Phase 4 路由的集成环境验收高德确认、来源正文可读性、扩展 SSE 和真实供应商 Usage。
