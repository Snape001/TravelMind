# Agent Runtime

`travelmind.runtime.workflow.build_research_graph` 编排 Research Director、Planner、专家研究、交叉质询、Writer 和 Reviewer。模型请求复用 `AgentInput`，节点结果使用现有的 `AgentOutput` 类型。`ResearchState` 在 Runtime 内部流转。

## 调研流程

1. Research Director 整理 `ResearchBrief`。影响研究方向的信息缺失时，持久化工作流发布澄清问题并暂停在 Checkpoint。用户答复后，Worker 恢复同一任务并重新整理简报。
2. Planner 从历史文化、自然地理、旅游资源、社交舆情和旅游规划五个角色中选出 3–5 个专家，生成任务目标和依赖。图校验专家名册与依赖关系，并生成 `ResearchPlan`。
3. Runtime 每轮通过 LangGraph `Send` 并行派发依赖已结束的任务。成功的依赖提供结论与引用。必要依赖失败时，其下游专家会收到 `DEPENDENCY_FAILED`，其他任务继续。没有可用研究结果时任务失败。
4. 在 `durable-v2` 中，专家为自己的发现核对证据评分、出处与冲突。Research Director 依据各分工状态与预算协调补查，并在关键冲突时安排定向质询。`durable-v1` 任务继续原有的专家两两质询流程。[专家反思与恢复](expert-reflection.md)
5. Writer 汇总已支持的结论、争议及证据缺口。Reviewer 独立检查报告正文、证据等级、原文支撑与署名。Writer 带审核意见修订，默认最多返工一次。审核通过后，图发布 `report.ready`，耗尽返工次数则以 `REVIEW_REJECTED` 结束。

## 执行与持久化

- FastAPI 提交任务，独立 Worker 从 PostgreSQL 领取并执行工作流。`ResearchRetriever` 在专家生成引用前保存证据并返回数据库确认的 Evidence ID。进程和恢复模型见 [Worker 运行说明](durable-runtime.md)。
- `RunBudget` 约束每次模型和检索操作的超时、尝试次数和模型输出 Token。任务层的 `max_attempts` 限制执行租约次数，具体规则见 [Phase 2 公共契约](phase-2-contract.md#重试预算与领取结果)。
- 节点输出与事件先写入 Checkpoint，再投影 Agent 执行记录、业务资源和 AgentEvent v1。SSE 重连可从上次接收的序号继续回放。
- 模型调用将证据、澄清答复、专家成果和审核意见作为独立 JSON 数据传递。模型输出按 Pydantic 校验，结论和报告引用进一步核对 Evidence ID，报告须经过独立审核。
