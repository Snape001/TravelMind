# TravelMind 开发进度

## 阶段 0 · 基础架构与接口协议

- **完成情况**　Next.js 与 FastAPI 骨架已运行。已定义 LangGraph State、Pydantic Agent 输入输出、AgentEvent v1 和任务 API。Alembic 首版迁移包含任务、事件、Agent 执行、证据和报告五张表。
- **本地环境**　`backend/.env.example` 与本地 `backend/.env` 已配置。Compose 通过本机 `15432` 和 `16379` 端口提供 PostgreSQL 和 Redis，启动说明见 [README](README.md)。
- **验收结果**　后端 9 项测试通过，其中 PostgreSQL 集成测试使用 Compose 中的专用测试数据库。两个容器健康检查通过，Redis 返回 `PONG`，Alembic 位于 `0001 (head)`，FastAPI 就绪、任务创建和查询接口均已实测。阶段 0 的前端生产构建与经 Next.js 转发的 SSE 续传已通过验证。
- **DeepSeek 验收（2026-11-14）**　通过后端模型工厂向官方 Chat Completions API 发送一次 `deepseek-flash` 请求。模型返回 `OK`，结束原因为 `stop`，耗时 0.47 秒，使用 9 个输入 Token 和 1 个输出 Token。
- **下一阶段**　接入 Research Director、Planner 和任务调度。
- **基础提交**　`01c8e60`。

## 阶段 1 · C 线前端（2026-11-14）

- **完成情况**　首页支持输入目的地、主题、个性化需求和调研深度。专家公会提供三级分组和 10 份专家档案。调研工作台展示动态分工、任务依赖、Agent 对话、工具执行和报告返工。报告中心提供归档，阅读页支持章节目录、署名和证据跳转。
- **接口与事件**　前端模拟路由复用生成的 OpenAPI 类型，覆盖任务、报告和证据读取。模拟 SSE 使用 AgentEvent v1，支持 `after`、`Last-Event-ID`、去重和心跳。后端代码和公共契约保持原样。
- **验收结果**　10 项前端测试通过，ESLint、TypeScript 类型检查和 Next.js 生产构建通过。本机 HTTP 实测了 8 个页面及资源地址、幂等任务创建、422 校验、跨任务报告的 404 及 SSE 游标优先级。浏览器完成成都模拟任务、组织图与对话联动、报告引用、归档及专家档案验证。
- **遗留与下一步**　模拟任务和报告内容在服务重启后清空，预置示例依然可用。后续联调用真实任务、证据、报告接口与 AgentEvent SSE 替换模拟传输，并验证异常、重连和持久化链路。
- **前端提交**　`db9b35d`，独立分支 `codex/frontend`。
