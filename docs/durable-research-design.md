# TravelMind 持久化调研与任务控制

架构设计基于 `main@6822d61`。具体状态转换、接口及字段以 [Phase 2 公共契约基线](phase-2-contract.md) 为准。PostgreSQL 保存任务、待执行指令、权限与公开事件。独立 Worker 执行 LangGraph，使用 PostgreSQL Checkpointer 推进或续跑。Redis 用于可选的唤醒通知，Worker 定时轮询 PostgreSQL 兜底。

## 当前实现

- 创建任务和首条 `queued` 事件已在同一事务内提交。FastAPI 随后通过 `BackgroundTasks` 启动图。行锁能阻止同一任务重复领取，但 `running` 没有租约或重领机制，现有幂等键也跨用户全局唯一。任务、Agent 执行、证据、报告和事件表已在 Alembic `0001` 建立。见 [任务 API](../backend/travelmind/api/tasks.py)、[任务存储](../backend/travelmind/db/tasks.py) 和 [数据模型](../backend/travelmind/db/models.py)。
- 当前图使用 `builder.compile()`，锁文件中的 `langgraph-checkpoint` 是传递依赖，尚无已接入的 Checkpointer。Director 追问后直接结束图。Worker 从图的内存事件流逐条写入 PostgreSQL。Redis 已配置连接和 PING，就绪检查依赖该结果。见 [工作流](../backend/travelmind/runtime/workflow.py)、[Worker](../backend/travelmind/runtime/worker.py) 和 [健康检查](../backend/travelmind/api/health.py)。
- SSE 已按任务序号回放，`Last-Event-ID` 优先于 `after`。任务依赖目前按 UUID 取记录，缺少身份与归属检查。页面用同源 `EventSource`，收到 `needs_clarification` 会关闭连接。见 [事件协议](../backend/travelmind/schemas/events.py)、[任务 API](../backend/travelmind/api/tasks.py) 和 [工作台](../frontend/src/components/workbench.tsx)。

## 任务状态机

复用现有六个公开状态。暂停请求由 `pause_requested_at` 表达，实际暂停后发布 `task.status(paused)`。

| 当前状态 | 触发 | 下一状态 |
| --- | --- | --- |
| 新任务 | 创建并提交首条事件 | `queued` |
| `queued` | Worker 领取 | `running` |
| `queued` | 用户暂停 | `paused`，撤销可领取标记 |
| `running` | 用户请求暂停 | 先记录暂停请求。在下一安全边界提交 Checkpoint 后进入 `paused` |
| `running` | 图的澄清节点中断 | `needs_clarification` |
| `paused` | 用户恢复 | `queued`，提交新的可领取指令 |
| `needs_clarification` | 用户提交当前轮答案 | `queued`，保存答案与续跑指令 |
| `running` | 租约过期或可重试故障 | 有界退避后回到 `queued`。耗尽预算进入 `failed` |
| `running` | 审核通过或永久失败 | `completed` 或 `failed`，均为终态 |

暂停是协作式的。Worker 在 Director、Planner、每轮并行专家批次、交叉质询和报告审核等边界检查请求，在已启动节点结束后停止派发。若终态或待澄清先落库，该结果优先并清除暂停请求。暂停已排队的续跑任务时保留其答案和续跑指令。租约回收时先对账最新 Checkpoint，再处理待执行的暂停或续跑指令。

## Worker、Checkpoint 与事件

1. **领取与回收**。FastAPI 在同一事务创建任务、写首条事件及可领取标记。Worker 按 `available_at` 使用 `FOR UPDATE SKIP LOCKED` 领取，提交后再调用外部服务。领取时写入租约和递增的 `lease_epoch`，心跳续租。Checkpointer 的 `aput` 与 `aput_writes` 均需持有任务行锁并在同一数据库事务验证 epoch 与租约后写入，业务表写入也遵循此规则。过期 Worker 停止推进，旧 epoch 的写入被拒绝。Redis 通知在事务提交后发送，丢失时靠轮询捞取。 [PostgreSQL 领取语义](https://www.postgresql.org/docs/17/sql-select.html#SQL-FOR-UPDATE-SHARE)
2. **续跑**。用 `thread_id = str(task_id)` 和 `AsyncPostgresSaver`，以 `durability="sync"` 提交边界。首次运行传 `ResearchInput`，已有未完 Checkpoint 的崩溃恢复使用 `None` 输入。人工暂停和澄清由独立的控制节点调用 `interrupt()`，完成阶段的结果先进入 Checkpoint。恢复时用 `Command(resume=...)` 向匹配的中断提交继续信号或答案。恢复会从中断节点开头执行，因此调用模型、检索及生成新 ID 的逻辑不放在中断之前。澄清答案按 `clarification_id` 记录消费标记，Worker 重领时检查消费标记或当前中断，避免旧答案进入下一轮。 [LangGraph Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers) · [中断与续跑](https://docs.langchain.com/oss/python/langgraph/interrupts)
3. **崩溃一致性**。为 Agent 执行、报告版本和可回放事件分配稳定的操作标识。将每步待发布的业务结果和事件保留在 Checkpoint 中，先持久化，再投影到业务表。投影事务同时写入资源、事件及投影进度。Worker 重领后先补齐断点之后未投影的结果，再推进图。现有证据写入继续按任务和来源去重并返回正式 Evidence ID。仍在执行且结果尚未持久化的外部模型或检索请求可能重发，重试受次数与超时约束。 [LangGraph 故障恢复](https://docs.langchain.com/oss/python/langgraph/fault-tolerance)
4. **AgentEvent v1**。保留 envelope、命名事件和固定的任务内递增序号，前端仍按 `event_id` 去重。`task.clarification.questions` 仍是字符串数组，收到该事件后前端重新读取任务详情获取提问 ID。澄清记录、提示事件和待澄清状态同事务提交。发布 `evidence.added`、`report.reviewed` 或 `report.ready` 时相关资源须已可读。终态事件与任务终态同事务提交。暂停和澄清页面保留 SSE 或在操作后以最后序号重连，完成或失败后关闭。

## API 与身份

沿用 `/api/v1`。现有任务创建、详情、报告、证据和 SSE 路径及响应字段继续有效。任务详情增补 `pause_requested_at` 与 `pending_clarification`，后者形如 `{clarification_id, questions: [{question_id, text}]}`，无待答问题时为 `null`。

| API | 请求与响应 |
| --- | --- |
| `POST /tasks` | 现有 `ResearchBrief`，创建返回 `201` 与 `Location`。同一用户的等价幂等请求返回 `200` |
| `POST /tasks/{task_id}/pause` | `queued` 同步暂停返回 `200 TaskRead`。`running` 接受暂停请求返回 `202 TaskRead` |
| `POST /tasks/{task_id}/resume` | 仅接受 `paused`，返回 `202 TaskRead` |
| `POST /tasks/{task_id}/clarifications/{clarification_id}/answer` | `{answers: [{question_id, text}]}`，校验当前轮和每个问题恰好一个非空答案。接受后返回 `202 TaskRead` |
| `GET /auth/login`、`GET /auth/callback` | 跳转到 OIDC 提供方，回调创建会话并跳转到工作台 |
| `GET /auth/me`、`POST /auth/logout` | 会话查询返回 `200 {user_id, display_name, csrf_token}`，注销返回 `204` |

控制请求要求 `Idempotency-Key`，按用户、任务与操作限定作用域。同键同载荷返回原响应，先查重再检查状态。同键不同载荷、状态不允许或澄清轮已过期返回 `409`。字段校验失败返回 `422`。暂停、恢复、答复与事件在锁定任务行后的同一事务内提交。

由 FastAPI 接入可配置的 OIDC 提供方，使用授权码和 PKCE，验证 `state`、`nonce` 及 ID Token。以 `(issuer, subject)` 映射本地用户，使用同源 `HttpOnly`、`Secure`、`SameSite=Lax` 的不透明会话 Cookie。写请求校验来源和 `X-CSRF-Token`。未登录返回 `401`。已登录访问他人任务及其事件、证据或报告统一返回 `404`。SSE 建连时校验归属，后续定期复核有效会话以处理注销和过期。 [OpenID Connect Core](https://openid.net/specs/openid-connect-core-1_0.html) · [OAuth 2.0 安全规范](https://www.rfc-editor.org/rfc/rfc9700)

## 数据库增量

| 表或迁移 | 增量 |
| --- | --- |
| `research_tasks` | 增加 `owner_id`、`available_at`、`lease_until`、`lease_epoch`、`attempt_count`、`pause_requested_at`、续跑指令与投影游标。增加领取、过期回收和所有者索引，创建幂等键改为 `(owner_id, idempotency_key)` 唯一 |
| `task_clarifications` | 保存任务、澄清轮 ID、问题列表、答案、内部中断 ID 和时间，同一任务最多一轮待答 |
| `task_commands` | 保存控制请求的幂等作用域、请求摘要及响应快照 |
| `agent_events` | 保留 `(task_id, sequence)` 主键，增加任务内唯一的投影去重标识，历史行可为空 |
| `users`、`sessions` | 用户唯一键为 `(issuer, subject)`，会话保存 token 哈希、所属用户、过期与撤销信息 |
| Checkpoint 表 | 用锁定版本的 `langgraph-checkpoint-postgres` 初始化，由部署迁移步骤执行一次。固定图版本，升级前验证已有 Checkpoint 可读 |

在停止旧后台执行器后做迁移。历史无主任务默认不可见，也不可领取，由管理员按明确映射归属。已启动且无 Checkpoint 的旧任务标记为失败并写入事件，原报告和证据保留。历史未启动任务可在绑定所有者后由 Worker 首次执行。

## 三个开发分支

先串行落一份契约基线，冻结状态转换、`TaskRead`、控制 API、错误格式、Worker 存储接口和新增字段。基线合入后，三个分支从该提交展开。合并顺序为任务服务与身份认证、Agent Runtime 与 Worker、Frontend。

| 分支 | 独占修改面 | 交接接口 |
| --- | --- | --- |
| 任务服务与身份认证 | `api/`、`db/`、`schemas/`、`main.py`、业务 Alembic 迁移 | 向 Worker 提供 `claim`、`renew`、`load_instruction`、`commit_projection` 和执行租约检查。向前端发布 OpenAPI 与 AgentEvent v1 示例 |
| Agent Runtime 与 Worker | `runtime/`、Worker 入口与 Checkpoint 初始化、`compose.yaml`、后端依赖和锁文件 | 消费领取结果 `{task_id, trace_id, lease_epoch, instruction}`。输出可幂等投影的状态、事件和资源。依赖变更由该分支统一纳入 |
| Frontend | `frontend/`，含唯一一份生成的 API 类型 | 消费公共 HTTP 和 SSE，先用契约 fixture 开发，合并前由最终后端 OpenAPI 重生成类型 |

`README.md` 与 `PROGRESS.md` 由集成方一次性汇总。合并验收覆盖两个 Worker 竞争、租约过期后旧 Worker 写入被拒、Checkpoint 与投影之间崩溃、Redis 中断期间继续领取、暂停和澄清跨进程重启、幂等答复、跨用户任务及 SSE 隔离。最后用真实服务跑浏览器链路，确认恢复后任务 ID 和事件序号连续，报告与证据可回读。
