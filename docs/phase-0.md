# 阶段 0 · 基础架构与接口协议

本文记录阶段 0 的公开契约和基础数据模型。阶段 1 的任务执行、证据落库与前端接入见 [Agent Runtime](agent-runtime.md) 和 [集成验收记录](../PROGRESS.md)。

## 目录职责

| 路径 | 职责 |
| --- | --- |
| `frontend/src/app/` | Next.js App Router 与页面入口 |
| `frontend/src/lib/api.generated.ts` | 从 FastAPI OpenAPI 生成的 HTTP 和 SSE 数据类型 |
| `backend/travelmind/api/` | HTTP 路由、健康检查和 SSE |
| `backend/travelmind/schemas/` | 公共接口与内部 Agent 数据契约 |
| `backend/travelmind/runtime/` | Agent Runtime，包含图状态、并行 Reducer 和模型工厂 |
| `backend/travelmind/db/`、`backend/alembic/` | 数据模型、任务事务与数据库迁移 |

Council Manager 通过 `ResearchBrief` 和 `ResearchPlan` 读写规划。Research Engine 的专家通过 `ResearchResult` 返回研究结果。Evidence Center 使用 `Evidence` 与 `Finding` 表示来源与结论。Report Engine 通过 `ReportDraft` 与 `Review` 交接撰写和审核结果。Agent Runtime 消费这些结构，FastAPI 通过独立的公共 `AgentEvent` 向前端传输进度。

## LangGraph State

`ResearchState` 是 Agent Runtime 内部的 `TypedDict`，由调度方传入 `task_id`、`trace_id` 和已验证的 `brief`。后续节点返回自己负责的字段更新。

| 字段 | 更新规则 |
| --- | --- |
| `task_id`、`trace_id` | 调度方初始化，贯穿整个任务 |
| `brief`、`status`、`plan` | Director 或协调节点在同一 superstep 内各自单写，更新时覆盖 |
| `results` | 以 `run_id` 为键合并各并行节点的 `AgentOutput` |
| `evidence` | 以 `evidence_id` 为键合并各并行节点的 `Evidence` |
| `draft`、`review_round` | 撰写或审核协调节点分别单写，更新时覆盖 |

两个并行字段使用相同的字典 Reducer。重复投递相同记录可安全重放。同一个 ID 对应不同内容时抛出冲突。每次新 Agent 执行分配独立的 `run_id`，重新投递已生成的输出时复用原 ID。流程顺序以任务依赖和公共事件序号为准。

## Agent 输入与输出

`AgentInput` 包含任务与追踪 UUID、单次执行 UUID、稳定的 `agent_id`、归一化后的 `brief`，以及可选的分配任务、已有证据、先前结论与报告草稿。预算字段包含单次模型请求超时、最大尝试次数和最大输出 Token。Agent Runtime 在阶段 0 之后负责整体执行预算与失败分类。

`AgentOutput` 以 `kind` 为判别字段，每种输出均包含 `run_id` 和 `agent_id`。

| kind | 业务内容 |
| --- | --- |
| `brief` | Director 整理后的 `brief` |
| `clarification` | 需向用户确认的 `questions` |
| `plan` | 包含目标、分配任务和任务依赖的 `plan` |
| `research` | 结构化 `findings` 和新增 `evidence` |
| `report` | 有章节、署名与证据引用的 `report` |
| `review` | 审核结论 `review`，需要返工时包含明细 |
| `failed` | 结构化 `error`，包含错误码、公开消息和可重试标记 |

`task_id`、`trace_id`、`assignment_id`、`run_id`、`evidence_id`、`event_id` 和 `report_id` 使用 UUID。`agent_id` 是 1–64 字符的小写稳定标识，例如 `history_culture`。规划依赖须指向同一计划中的已声明任务，且必须无环。证据需要原始 URL 或文献定位信息，受支持或存在争议的结论需要至少一个证据 ID。API 和事件的时间使用带时区的 ISO 8601 字符串。

## HTTP 接口

启动 FastAPI 后，`/docs` 提供交互式文档，`/openapi.json` 提供机器可读 Schema。浏览器可通过前端的同源 `/api/v1/*` 访问。

| 方法与路径 | 请求与响应 |
| --- | --- |
| `GET /health/live` | 进程存活，返回 `200` |
| `GET /health/ready` | 检查 PostgreSQL 的任务表与 Redis 的 PING。两者均可用时返回 `200`，否则返回 `503`。DeepSeek 字段仅表示密钥配置状态 |
| `POST /api/v1/tasks` | 请求体为 `ResearchBrief`。创建返回 `201` 与 `Location`。接受可选 `Idempotency-Key` |
| `GET /api/v1/tasks/{task_id}` | 返回 `TaskRead` |
| `GET /api/v1/tasks/{task_id}/events` | 返回 `text/event-stream`，事件数据为 `AgentEvent` |
| `GET /api/v1/tasks/{task_id}/evidence/{evidence_id}` | 返回指定任务中的 `EvidenceRead` |
| `GET /api/v1/tasks/{task_id}/reports/{report_id}` | 返回指定任务中的 `ReportRead` |

任务请求示例为 `{"destination":"泉州","topics":["历史文化"],"depth":"deep"}`。`depth` 可取 `quick`、`deep` 或 `academic`，默认为 `quick`。同一幂等键和等价请求体返回原任务与 `200`，请求体不同返回 `409`。`Location` 同样出现在重放响应中。省略幂等键时每次提交创建一个任务。

任务或资源不存在时返回 `404`。业务错误使用 `{"detail":{"code":"TASK_NOT_FOUND","message":"调研任务不存在","retryable":false}}`。参数或请求体验证失败使用 FastAPI 的 `422` 格式。

## AgentEvent v1

SSE 的 `id` 是当前任务内从 1 开始递增的十进制 `sequence`，`event` 对应 `payload.type`，`data` 是单行 JSON。每条消息以空行结束。

```text
id: 1
event: task.status
data: {"version":1,"event_id":"c8091234-bbcc-46ed-8c2b-8ac15ae91aca","task_id":"db453dff-fc8c-414b-8743-36a575be8e56","trace_id":"57820407-0ad0-46c0-adae-f3c0a7d90248","sequence":1,"occurred_at":"2026-11-14T12:00:00Z","payload":{"type":"task.status","status":"queued","error":null}}

```

首次连接默认从头回放，也可传 `?after=<sequence>`。重连时若有 `Last-Event-ID`，优先使用该值。游标是排他下界，仅返回更大的序号。后端按序读取 PostgreSQL 事件表并定期发送 SSE 注释心跳，连接保持到客户端关闭。客户端可按 `event_id` 去重。同一任务的后续事件写入方需要在事务中串行分配序号，例如先锁定任务行再读取最后序号，以保持提交与对外可见顺序一致。

当前由任务创建接口发布 `task.status`，状态为 `queued`。后续工作流可使用已定义的 `task.clarification`、`plan.ready`、`agent.started`、`agent.message`、`agent.finished`、`tool.started`、`tool.finished`、`evidence.added`、`report.ready` 和 `report.reviewed`。公开消息字段携带可展示的进度与结论。

## PostgreSQL 基础模型

| 表 | 主键 | 关键字段与约束 |
| --- | --- | --- |
| `research_tasks` | `task_id` | `trace_id` 唯一，可空的 `idempotency_key` 唯一，包含 `brief` JSONB、任务状态和时间 |
| `agent_events` | `(task_id, sequence)` | `event_id` 唯一，任务与追踪号组合外键，包含版本、发生时间和已校验的 `payload` JSONB |
| `agent_runs` | `run_id` | 任务外键、`agent_id`、可选的 `assignment_id`、状态、结构化结果及执行时间 |
| `evidence` | `evidence_id` | 任务外键、来源类型、原文摘录、链接或文献定位、发布时间和采集时间 |
| `reports` | `report_id` | 任务外键，`(task_id, version)` 唯一，结构化内容 JSONB |

由 Alembic 管理数据库演进。任务行与首个事件在同一个事务内保存。Redis 的连接与可用性检查已接入，供后续工作流协调使用。DeepSeek 模型工厂从后端配置读取模型名和密钥，并应用 Agent 预算中的模型调用参数。

技术参考。[LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api) · [FastAPI SSE](https://fastapi.tiangolo.com/tutorial/server-sent-events/) · [DeepSeek 首次调用](https://api-docs.deepseek.com/) · [DeepSeek 模型列表](https://api-docs.deepseek.com/api/list-models/)
