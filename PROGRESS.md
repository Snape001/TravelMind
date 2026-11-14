# TravelMind 开发进度

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
