# Phase 2 公共契约基线

本契约冻结持久化调研的 API、状态机与 Worker 交接。架构背景见 [持久化调研设计](durable-research-design.md)。本提交提供状态规则和共享 Schema。新增路由、存储实现与迁移由下文指定分支接入。

## 任务状态

继续使用 `TaskStatus` 的六个值。任务服务在持有任务行锁时调用 [next_task_status](../backend/travelmind/schemas/task_control.py)，并检查表中的业务前置条件。

| 触发 | 转换 | 前置条件与写入 |
| --- | --- | --- |
| `create` | 新任务 → `queued` | 持久化请求、初始指令及首条状态事件 |
| `claim` | `queued` → `running` | 到达可领取时间，写入租约并递增代数 |
| `request_pause` | `queued` → `paused` | 清除可领取时间，保留原执行指令及已保存的答案 |
| `request_pause` | `running` → `running` | 填写 `pause_requested_at`。重复请求保留首次时间 |
| `pause_reached` | `running` → `paused` | 有暂停请求且安全边界的中断已持久化。清除请求并释放租约 |
| `clarification_required` | `running` → `needs_clarification` | 保存 Checkpoint 中断和待答轮次。清除暂停请求并释放租约 |
| `resume` | `paused` → `queued` | 若已停在暂停中断，创建续跑指令。若暂停发生在排队时，恢复原指令 |
| `answer` | `needs_clarification` → `queued` | 当前轮答案及匹配的续跑指令同事务提交 |
| `retry` | `running` → `queued` | 可重试故障或租约过期，且尚有预算。保留待处理指令及暂停请求，依据退避时间再次领取 |
| `complete` / `fail` | `running` → `completed` / `failed` | 持久化最终结果或不可重试错误。清除暂停请求和租约 |

`completed` 和 `failed` 为终态。已持久化的终态或澄清中断先于同一步的暂停请求。暂停仅在安全边界生效，正在执行的并行批次可先完成。在退避期间发起暂停按 `queued` 处理，恢复时仍须遵守任务的重试预算。

## HTTP 与 AgentEvent v1

所有下表路径位于 `/api/v1`。采用 [共享 Pydantic Schema](../backend/travelmind/schemas/api.py)。`TaskRead` 保留 `task_id`、`trace_id`、`brief`、`status`、`created_at`、`updated_at`。增补以下两个可空字段，历史记录缺字段时解析为 `null`。

| 字段 | 类型与含义 |
| --- | --- |
| `pause_requested_at` | 带时区的 ISO 8601 时间，表示正在等待 Worker 确认的暂停请求 |
| `pending_clarification` | `{clarification_id, questions: [{question_id, text}]}`。ID 为 UUID，问题至少一条且 ID 唯一。答复接受后清空 |

| 方法与路径 | 契约 |
| --- | --- |
| `POST /tasks` | 保留 `ResearchBrief`、可选幂等键和 `Location`。新建返回 `201 TaskRead`，同键同请求回读原任务并返回 `200 TaskRead` |
| `POST /tasks/{task_id}/pause` | 无请求体。排队中返回 `200 TaskRead`，执行中返回 `202 TaskRead`。执行中的响应仍为 `running`，包含 `pause_requested_at` |
| `POST /tasks/{task_id}/resume` | 无请求体，仅接受 `paused`。返回 `202 TaskRead`，状态为 `queued` |
| `POST /tasks/{task_id}/clarifications/{clarification_id}/answer` | 请求体为 `ClarificationAnswers`，形如 `{answers: [{question_id, text}]}`。要求当前轮每题恰好作答一次，文本去除首尾空白后为 1–4000 字符。返回 `202 TaskRead`，状态为 `queued` |
| `GET /tasks/{task_id}`、`GET /tasks/{task_id}/events` | 保留任务详情与 SSE。报告和证据查询路径继续沿用 |
| `GET /auth/login`、`GET /auth/callback` | 登录发起与回调成功均返回 `302`。前者跳转到身份提供方，后者建立会话后跳转到同源工作台 |
| `GET /auth/me`、`POST /auth/logout` | 会话查询返回 `200 CurrentUser`，含 `user_id`、可空的 `display_name` 和 `csrf_token`。注销撤销会话、清除 Cookie 并返回 `204` |

继续使用 [AgentEvent v1](../backend/travelmind/schemas/events.py) 的字段、命名事件、任务内序号、心跳和回放规则。SSE `id` 仍为 `sequence`，`Last-Event-ID` 优先于 `after`，两者均为排他游标。Worker 持久化澄清中断后，任务服务在一个事务中写入澄清记录、`task.clarification` 和 `task.status(needs_clarification)`。事件中的 `questions` 仍为字符串数组，前端通过 `GET /tasks/{task_id}` 获取问题 ID。暂停请求被接受时和执行实际暂停后，前端以 API 响应和后续状态事件分别更新界面。暂停或待答期间可以保持 SSE，也可从最后序号重连。所有报告或证据事件发布前，对应资源须可读。

### 错误与幂等

业务错误复用 `ErrorResponse`，即 `{"detail":{"code":"TASK_STATE_CONFLICT","message":"任务状态不支持该操作","retryable":false}}`。`retryable` 表示原请求是否适合重试。

| HTTP | 错误码 |
| --- | --- |
| `400` | `AUTH_CALLBACK_INVALID`，登录回调上下文或身份凭据无效 |
| `401` / `403` | `UNAUTHENTICATED` 或 `CSRF_INVALID` |
| `404` | `TASK_NOT_FOUND`、`REPORT_NOT_FOUND`、`EVIDENCE_NOT_FOUND`。未拥有的任务统一返回 `TASK_NOT_FOUND` |
| `409` | `IDEMPOTENCY_CONFLICT`、`TASK_STATE_CONFLICT`、`CLARIFICATION_STALE` |
| `422` | 新端点使用 `VALIDATION_ERROR`，包括缺少幂等键、格式无效或答复题目集合不匹配。既有端点保留 FastAPI 的 `detail[]` |
| `500` / `503` | 复用 `DATABASE_ERROR` / `DATABASE_UNAVAILABLE` |

`Idempotency-Key` 为 1–128 个可见 ASCII 字符，区分大小写。控制端点必填，唯一范围为 `(owner_id, task_id, operation, key)`，其中 `operation` 取 `pause`、`resume` 或 `answer`。创建按 `(owner_id, key)` 去重。以验证后的请求内容计算摘要，对象键排序、应用 Schema 默认值与文本归一化。答复摘要包含路径的 `clarification_id`，答案按 `question_id` 排序。

先校验会话、归属和请求格式，再查重，最后校验当前状态与澄清轮次。控制请求同键同载荷重放第一次成功响应的状态码和快照，同键异载荷返回 `409`。新键请求非当前待答轮次返回 `CLARIFICATION_STALE`，暂停或恢复的状态冲突返回 `TASK_STATE_CONFLICT`。成功操作及幂等记录同事务提交。

### 身份与所有权

FastAPI 接入配置的 OIDC 提供方，使用授权码和 PKCE，验证 `state`、`nonce`、ID Token 签名、签发方、受众和过期时间。用户以 `(issuer, subject)` 唯一映射本地 `user_id`。用同源 `HttpOnly`、`Secure`、`SameSite=Lax` 的不透明会话 Cookie，服务端仅存会话令牌哈希。写请求校验来源与 `X-CSRF-Token`，令牌由 `/auth/me` 返回。 [OIDC Core](https://openid.net/specs/openid-connect-core-1_0.html) · [OAuth 2.0 安全规范](https://www.rfc-editor.org/rfc/rfc9700)

任务服务从已验证会话设置 `owner_id`，客户端及 Worker 消息均不能指定归属。每次读写先判断任务归属，报告及证据再按所属任务查找。SSE 连接前同样鉴权，连接期间定期检查会话是否过期或撤销。认证端点由 Next.js 经同源 `/api/v1` 代理转发。

## Worker 存储接口

[WorkerStore](../backend/travelmind/schemas/worker.py) 是异步 Python 协议。存储适配器以数据库时钟和事务实现以下约定。Redis 通知只负责提前唤醒。

| 方法 | 返回与责任 |
| --- | --- |
| `claim(worker_id, lease_seconds=...)` | 返回 `TaskClaim` 或 `None`。用 `FOR UPDATE SKIP LOCKED` 领取到期的 `queued` 行。可回收过期的 `running` 行，先持久化新租约并递增代数，`reclaimed=true` 指示 Worker 先对账 Checkpoint 再决定续跑、待答、暂停或终态 |
| `renew(lease, lease_seconds=...)` | 返回新到期时间。仅匹配任务、Worker、代数且未过期的 `running` 租约可续期 |
| `load_instruction(lease)` | 返回持久化的 `WorkerInstruction`，含指令、暂停请求和最近已提交的投影序号 |
| `commit_projection(lease, projection)` | 接受 `ProjectionBatch` 或 `AttemptFailure`。按序原子写入相关状态、指令消费、澄清、AgentRun、报告与事件。返回 `applied` 或 `already_applied` |
| `check_lease(lease, transaction=...)` | 在调用者的写事务中执行任务行锁和租约校验，校验与实际写入必须使用同一个数据库事务。Checkpoint 的 `aput`、`aput_writes` 和提前落库的证据都适用 |

租约失效抛出 `LeaseLost`，Worker 应停止写入。冲突的投影、跳号和非法状态转换抛出 `ProjectionConflict`。投影按任务从 1 连续编号。持有有效租约时，已提交序号可重放相同 Checkpoint 和载荷，异内容报冲突。若提交已释放租约，原 Worker 重试会收到 `LeaseLost`。事件以 `(task_id, projection_key)` 去重。状态与澄清事件由存储根据转换生成，Worker 的批次携带其余 `EventPayload`。尚未形成可投影结果的执行故障可用稳定的 `failure_id` 幂等结算，存储按 `retryable` 和预算决定重排或失败。超期回收发现已完成的 Checkpoint 时优先补齐投影，再释放租约。

指令分为 `start`、`continue_checkpoint`、`resume_pause` 和 `answer_clarification`。无 Checkpoint 的 `start` 使用指令中的 `ResearchBrief`。若已持久化启动状态，则使用同一 `thread_id=str(task_id)` 从已有 Checkpoint 续跑。恢复暂停或回答澄清时必须匹配持久化的中断 ID。图中记录已消费的 `instruction_id`，Worker 重领后据此避免把同一答案送到下一轮，任务服务随投影提交消费记录。

## 数据库与分支归属

| 归属 | 字段与迁移 |
| --- | --- |
| 任务服务与身份认证 | 在 `0001` 后新增业务迁移。`research_tasks` 增加 `owner_id`、`available_at`、`lease_worker_id`、`lease_until`、`lease_epoch`、`attempt_count`、`pause_requested_at`、`instruction`（JSONB）、`paused_interrupt_id` 和 `projected_index`。新增所有者、领取及租约回收索引，创建键改为 `(owner_id, idempotency_key)` 唯一 |
| 同上 | `task_clarifications` 按 `(task_id, clarification_id)` 保存问题、答案、`interrupt_id`、创建及答复时间，同一任务仅一轮待答。`task_commands` 持久化控制请求的作用域、摘要及响应快照 |
| 同上 | `agent_events` 加入可空的 `projection_key` 及任务内唯一约束。增加 `task_projections` 记录任务内投影序号、Checkpoint ID 和载荷摘要，并按任务记录已结算的 `failure_id` |
| 同上 | `users` 保存用户 UUID 和唯一的 `(issuer, subject)`。`sessions` 保存会话哈希、用户、过期与撤销时间 |
| Agent Runtime 与 Worker | 负责 PostgreSQL Checkpointer 版本、独立初始化步骤和图版本。部署时先初始化 Checkpoint 表，再启动 Worker |
| Frontend | 负责 `frontend/` 的功能和生成类型，待任务服务发布最终 OpenAPI 后重新生成 |

`lease_epoch` 和 `projected_index` 使用非负 `bigint`，初始为 0。`attempt_count` 以非负整数计数，首次领取及故障重领增加，用户正常暂停、恢复及答复开启新的尝试预算，暂停排队中的故障重试保留原预算。`lease_worker_id` 为 UUID，和 `lease_until` 同时写入或清空。时间字段使用 `timestamptz`，`available_at=NULL` 表示不可领取。有主任务的 `owner_id` 引用 `users.user_id`。历史无主任务允许暂为空。

迁移时先停止旧执行器。历史无主任务默认不可见、不可领取，按明确映射绑定用户。旧执行器已启动且缺少 Checkpoint 的未完成任务应标记为失败并追加状态事件。保留历史报告、证据和事件编号。

三个分支从本契约提交出发。任务服务分支独占后端 `api/`、`db/`、公共 `schemas/` 和业务 Alembic。Runtime 分支独占 `runtime/`、Worker 入口、Checkpoint 初始化及后端依赖锁。先合任务服务，再合 Runtime，最后合 Frontend。验收包含并发领取、过期租约写入防护、投影重放、停机续跑、澄清幂等、跨用户隔离及 SSE 回放。
