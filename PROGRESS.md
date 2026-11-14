# TravelMind 开发进度

## Phase 2 · 公共契约基线

- **完成情况**　冻结六种任务状态、暂停与恢复、澄清答复、错误和幂等规则，补充 `TaskRead`、澄清与会话 Schema，并定义带租约校验的异步 `WorkerStore` 协议。数据库字段、迁移责任与任务所有权约定见 [公共契约基线](docs/phase-2-contract.md)。
- **验收结果**　后端 42 项测试通过，含基于 Alembic 初始化的专用 PostgreSQL 集成测试。前端 10 项测试及 TypeScript 检查通过。Ruff、Python 锁文件和 Alembic 一致性检查通过。FastAPI 测试客户端验证服务启动、OpenAPI 兼容性及已有任务接口。
- **下一步**　任务服务实现新增路由、OIDC 和业务迁移，Runtime 实现租约存储对接与 Checkpoint 续跑，Frontend 接入控制与认证流程并生成最终 API 类型。AgentEvent v1 沿用现有线协议。
- **交付提交**　`feat: freeze Phase 2 public task contracts`。

## 阶段 1 · 三分支集成（2026-11-14）

| 顺序 | 功能分支 | 分支头 | 合并提交 |
| --- | --- | --- | --- |
| 1 | Agent Runtime | `c43d21e` | `e7ceffc` |
| 2 | Research Engine | `57243f7` | `72ef565` |
| 3 | Frontend | `f367119` | `1695017` |

- **冲突与接口**　Research Engine 和 Frontend 合并时均在本进度文件发生冲突，三条功能线的原始验收记录均保留。Runtime 复用 Pydantic Agent 输入输出和 `Evidence`。检索在生成引用前写入 PostgreSQL 并复用已存储的证据 ID。Worker 保存 Agent 执行与各审核版本，使用现有 `AgentEvent v1` 记录可续传事件。Next.js 默认走同源真实 API，支持通过环境变量进入原有模拟模式。重新生成 OpenAPI 类型后无差异。
- **回归测试**　后端 36 项通过，使用从 Alembic 首版迁移起建的专用 PostgreSQL 库。前端 10 项通过。Ruff、ESLint、TypeScript 和 Next.js 生产构建均通过。Alembic 一致性检查、Python 锁文件与依赖检查、npm 锁定依赖检查均通过。npm 生产依赖审计报告 0 项漏洞。
- **真实端到端**　Chrome 经 Next.js 提交福建泉州深度调研，用真实 DeepSeek、博查、Firecrawl 和 Compose PostgreSQL、Redis 验证。5 位专家生成 69 条事件，报告经过一次返工后获批，包含 5 个章节和 15 条去重引用。验证工作台、报告归档、来源跳转、各审核版本的引用读回，以及从 `Last-Event-ID: 32` 回放第 33–69 条事件。原有示例工作台和示例报告仍可访问。
- **联调修复**　关闭了导致浏览器事件缓冲的 Next.js 压缩。模型的无效或截断响应进入有界重试，撰写与审核使用结论和草稿引用到的证据。部分 Firecrawl 抓取超时后使用已取得的搜索摘要，最终任务正常完成。
- **遗留问题**　后台任务仍在 FastAPI 进程内运行，重启续跑、暂停及澄清后继续原任务的 HTTP 接口待实现。现有部署面向本地开发，用户身份与任务所有权校验待接入。

## 阶段 0 · 基础架构与接口协议

- **完成情况**　Next.js 与 FastAPI 骨架已运行。已定义 LangGraph State、Pydantic Agent 输入输出、AgentEvent v1 和任务 API。Alembic 首版迁移包含任务、事件、Agent 执行、证据和报告五张表。
- **本地环境**　`backend/.env.example` 与本地 `backend/.env` 已配置。Compose 通过本机 `15432` 和 `16379` 端口提供 PostgreSQL 和 Redis，启动说明见 [README](README.md)。
- **验收结果**　后端 9 项测试通过，其中 PostgreSQL 集成测试使用 Compose 中的专用测试数据库。两个容器健康检查通过，Redis 返回 `PONG`，Alembic 位于 `0001 (head)`，FastAPI 就绪、任务创建和查询接口均已实测。阶段 0 的前端生产构建与经 Next.js 转发的 SSE 续传已通过验证。
- **DeepSeek 验收（2026-11-14）**　通过后端模型工厂向官方 Chat Completions API 发送一次 `deepseek-flash` 请求。模型返回 `OK`，结束原因为 `stop`，耗时 0.47 秒，使用 9 个输入 Token 和 1 个输出 Token。
- **下一阶段**　接入 Research Director、Planner 和任务调度。
- **基础提交**　`01c8e60`。

## 阶段 1 · A 线 Agent Runtime

- **完成情况**　Research Director 整理需求并在必要时追问。Planner 从五个专业角色中动态选择 3–5 位专家并规划依赖。LangGraph 按依赖并行派发研究，隔离单个专家的失败，编排专家交叉质询、报告撰写、独立审核和有限返工。
- **验收结果**　后端 17 项测试通过，含 8 项工作流用例和 2 项 PostgreSQL 集成用例。Ruff 检查通过。FastAPI 的 `/health/live`、`/health/ready` 和 `/openapi.json` 均返回 `200`。
- **DeepSeek 实测（2026-11-14）**　向 `deepseek-flash` 提交泉州调研，并注入来自 UNESCO 的材料摘录。最终版本动态选择 3 位专家，完成交叉质询与一次返工，产出 3 节正文和 2 处证据引用。另一次模糊目的地请求返回 `needs_clarification` 和 2 个问题。
- **下一步**　对接真实检索 Adapter、任务执行持久化和 SSE 事件落库。图入口及接入方式见 [A 线 Agent Runtime](docs/agent-runtime.md)。
- **Git 提交**　`feat: implement A-line research runtime`（本次提交）。

## 阶段 1 · B 线 Research Engine（2026-11-14）

- **完成情况**　接入博查 Web Search、Firecrawl Scrape 和 OpenAlex Works。统一异步工具接口返回现有 `Evidence` 与提供方错误，支持并行检索、部分失败及有界重试。提供 PostgreSQL 写入，按任务去重并复用既有 Evidence ID。证据可通过现有任务 API 读取。
- **验收结果**　独立 PostgreSQL 测试库从 Alembic 首版迁移启动，后端 23 项测试通过，覆盖并发写入、旧证据复用、事务回滚和现有 API 读回。Ruff 检查通过。真实 OpenAlex 匿名检索返回 2 条文献，两条均成功入库并通过证据 API 读回。
- **博查与 Firecrawl 实测（2026-11-14）**　通过 `ResearchEngine.search` 搜索「泉州 世界文化遗产」，博查返回 HTTP 200，耗时 0.30 秒，转换为 3 条 Evidence，摘要分别为 221、221 和 701 个字符。通过 `ResearchEngine.scrape` 抓取 UNESCO 泉州世界遗产页面，Firecrawl 返回 HTTP 200，耗时 5.38 秒，转换为 1 条含 15,775 字符 Markdown 正文的 Evidence。四条 Evidence 均通过 JSON 序列化和反序列化校验。
- **下一步**　由任务工作流调用 Research Engine 并引用数据库返回的 Evidence ID。
- **交付提交**　`codex/research-engine` 分支的 `feat: add research engine and evidence storage`。配置和调用详见 [Research Engine 接入说明](docs/research-engine.md)。

## 阶段 1 · C 线前端（2026-11-14）

- **完成情况**　首页支持输入目的地、主题、个性化需求和调研深度。专家公会提供三级分组和 10 份专家档案。调研工作台展示动态分工、任务依赖、Agent 对话、工具执行和报告返工。报告中心提供归档，阅读页支持章节目录、署名和证据跳转。
- **接口与事件**　前端模拟路由复用生成的 OpenAPI 类型，覆盖任务、报告和证据读取。模拟 SSE 使用 AgentEvent v1，支持 `after`、`Last-Event-ID`、去重和心跳。后端代码和公共契约保持原样。
- **验收结果**　10 项前端测试通过，ESLint、TypeScript 类型检查和 Next.js 生产构建通过。本机 HTTP 实测了 8 个页面及资源地址、幂等任务创建、422 校验、跨任务报告的 404 及 SSE 游标优先级。浏览器完成成都模拟任务、组织图与对话联动、报告引用、归档及专家档案验证。
- **遗留与下一步**　模拟任务和报告内容在服务重启后清空，预置示例依然可用。后续联调用真实任务、证据、报告接口与 AgentEvent SSE 替换模拟传输，并验证异常、重连和持久化链路。
- **前端提交**　`db9b35d`，独立分支 `codex/frontend`。
