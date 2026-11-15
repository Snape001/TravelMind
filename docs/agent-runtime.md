# Agent Runtime

`travelmind.runtime.workflow.build_research_graph` 编排 Research Director、Planner、专家研究、交叉质询、Writer 和 Reviewer。模型请求复用 `AgentInput`，节点结果使用现有的 `AgentOutput` 类型。`ResearchState` 在 Runtime 内部流转。

## 调研流程

1. Research Director 整理 `ResearchBrief`。影响研究方向的信息缺失时，持久化工作流发布澄清问题并暂停在 Checkpoint。用户答复后，Worker 恢复同一任务并重新整理简报。
2. Planner 从历史文化、自然地理、旅游资源、社交舆情和旅游规划五个角色中选出 3–5 个专家，生成任务目标和依赖。图校验专家名册与依赖关系，并生成 `ResearchPlan`。
3. Runtime 每轮通过 LangGraph `Send` 并行派发依赖已结束的任务。成功的依赖提供结论与引用。必要依赖失败时，其下游专家会收到 `DEPENDENCY_FAILED`，其他任务继续。没有可用研究结果时任务失败。
4. `durable-v2` 按分工执行[专家反思](#专家反思)。`durable-v1` 任务继续原有的专家两两质询流程。
5. Writer 汇总已支持的结论、争议及证据缺口。Reviewer 独立检查报告正文、原文支撑、证据等级、时效和署名。引用低等级或未经核验的证据会送回修订。默认最多返工一次。审核通过后，图发布 `report.ready`，耗尽返工次数则以 `REVIEW_REJECTED` 结束。

## 专家反思

Worker 使用 `EvidenceQualityScorer` 为已入库的证据评分。每位专家生成候选结论，再按原始 Evidence ID 核验相关性、出处与冲突。一次分工有多个问题时，按冲突、低质量、缺证据的顺序处理。Research Director 依据反思结果决定定向补查。一次调研最多安排一次冲突质询，可邀请两位其他专家各答复一次。 [反思状态和停止条件](phase-3-contract.md#reflectionstate-与-reflectionaction)

- 快速、深度、学术调研分别最多补查 0、1、2 轮。每轮至多执行一项定向检索。检索词归一化后去重。补查无新来源时保留缺口。
- 每项分工最多预留 57,344 个输出 Token。每次模型输出至多 2,048 Token。每轮至多评分两批，每批至多四条证据。候选结论、反思判断和受邀专家的答复共用分工额度，各操作沿用 `RunBudget` 的超时和重试规则。超过评分容量的材料仍留在原始证据快照。
- 分工开始时保存 UTC 参考时间。开放时间、票价、预约和交通等时效敏感主题使用 365 天窗口。各轮复用同一参考时间和窗口。
- 各轮快照保存在 `reflection_rounds`，追加检索的决策、预算和质询名额先写入 Checkpoint，再派发。续跑复用已提交的结果。专家首轮检索前发出开始事件，终态结果存入 `expert_research` 时发出结束事件。同一分工的各轮共用运行 ID。

新任务默认用 `durable-v2`，存量任务按 Checkpoint 记录的版本续跑。部署前先停止旧 Worker，再启动新版。 [Worker 恢复与投影](durable-runtime.md)

## 执行与持久化

- FastAPI 提交任务，独立 Worker 从 PostgreSQL 领取并执行工作流。`ResearchRetriever` 在专家生成引用前保存证据并返回数据库确认的 Evidence ID。进程和恢复模型见 [Worker 运行说明](durable-runtime.md)。
- `RunBudget` 约束每次模型和检索操作的超时、尝试次数和模型输出 Token。任务层的 `max_attempts` 限制执行租约次数，具体规则见 [Phase 2 公共契约](phase-2-contract.md#重试预算与领取结果)。
- 节点输出与事件先写入 Checkpoint，再投影 Agent 执行记录、业务资源和 AgentEvent v1。SSE 重连可从上次接收的序号继续回放。
- 模型调用将证据、澄清答复、专家成果和审核意见作为独立 JSON 数据传递。模型输出按 Pydantic 校验，结论和报告引用进一步核对 Evidence ID，报告须经过独立审核。
