# TravelMind

面向中国旅游目的地的多智能体调研平台。阶段 0 提供 Next.js 前端、FastAPI 后端、任务持久化、可续传的 SSE 和 Agent 契约。C 线提供以模拟数据运行的完整前端调研流程。

## 独立运行前端

从项目根目录执行。

```powershell
Set-Location frontend
npm ci
npm run dev -- --hostname 127.0.0.1
```

打开 `http://127.0.0.1:3000`，填写目的地、主题、个性化需求和调研深度。新任务约 16 秒内依次推送规划、专家执行、工具调用、证据、报告与审核事件。首页也提供泉州示例工作台和报告链接，可直接查看已完成的流程。

- 工作台使用 React Flow 展示三级组织架构和任务依赖。选择组织图中的专家可筛选对话与事件。报告阅读页提供目录、章节署名和证据跳转。
- 前端模拟路由位于 `/mock/api/v1/tasks`，按 OpenAPI 生成类型提供任务、报告、证据和 AgentEvent v1 SSE。模拟 SSE 使用命名事件、任务内序号、`after` 和 `Last-Event-ID`。正式 `/api/v1/*` 仍由 Next.js 转发至 FastAPI。
- 新任务、报告内容和事件保存在 Next.js 进程内存中。浏览器保留已完成报告的链接，服务重启后须重新创建新任务。预置的泉州示例可继续访问。

## 本地启动

需要 Node.js 20.9+、Python 3.11+、uv，以及 Docker Compose 或可连接的 PostgreSQL 和 Redis 实例。从项目根目录执行以下 PowerShell 命令。

```powershell
docker compose up -d --wait
if (-not (Test-Path backend/.env)) { Copy-Item backend/.env.example backend/.env }
Set-Location backend
uv sync --locked
uv run alembic upgrade head
uv run uvicorn travelmind.main:app --reload --host 127.0.0.1 --port 8000
```

Compose 将 PostgreSQL 和 Redis 分别绑定到本机的 `15432` 和 `16379` 端口。若已有 `backend/.env`，请核对其中的连接串与实际服务地址一致。

在 `backend/.env` 的 `DEEPSEEK_API_KEY` 填入密钥。模板默认使用官方 API `https://api.deepseek.com` 和 `deepseek-flash`，阶段 0 的任务接口可在密钥为空时使用。

从项目根目录另开一个终端启动前端。

```powershell
Set-Location frontend
npm ci
npm run dev -- --hostname 127.0.0.1
```

前端地址为 `http://127.0.0.1:3000`，FastAPI 文档为 `http://127.0.0.1:8000/docs`。前端将同源的 `/api/v1/*` 转发至 FastAPI。若后端地址不同，可将 `frontend/.env.example` 复制为 `frontend/.env.local`，再修改 `API_ORIGIN`。

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/ready
$task = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:3000/api/v1/tasks `
  -ContentType 'application/json' -Headers @{'Idempotency-Key' = [guid]::NewGuid().ToString()} `
  -Body (@{destination='泉州'; topics=@('历史文化'); depth='deep'} | ConvertTo-Json)
Invoke-RestMethod "http://127.0.0.1:3000/api/v1/tasks/$($task.task_id)"
curl.exe -N "http://127.0.0.1:3000/api/v1/tasks/$($task.task_id)/events"
```

事件流收到首条 `task.status` 后保持连接，可用 Ctrl+C 退出。任务初始状态为 `queued`，后续阶段接入任务调度和实际研究。

## 开发验证

- 后端目录执行 `uv run pytest` 和 `uv run ruff check .`。PostgreSQL 集成测试需要将 `TEST_DATABASE_URL` 指向已经执行首版迁移的专用测试数据库。测试会写入调研任务及事件。
- 前端目录执行 `npm run test`、`npm run lint`、`npm run typecheck` 和 `npm run build`。测试覆盖需求校验、SSE 帧、续传与去重、任务状态及报告引用。
- 公共契约改动后，启动后端并在前端目录运行 `npm run generate:api`，提交生成的 TypeScript 类型。
- 如需验证 DeepSeek 密钥、网络和所选模型，从后端目录运行 `uv run python -m travelmind.runtime.check_deepseek`。该命令只读取官方模型列表。

项目结构、接口和数据模型见 [阶段 0 接口与架构](docs/phase-0.md)。本地骨架尚未接入用户身份与任务所有权校验，请保持在本地开发环境运行。
