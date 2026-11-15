# TravelMind

面向中国旅游目的地的多智能体调研平台。Next.js 工作台通过 FastAPI 启动真实调研。LangGraph 编排专家并行检索、交叉质询、撰写与审核，工作台通过 SSE 展示进度，报告可以跳转到入库的来源证据。

## 泉州示例回放

从项目根目录执行以下命令，然后打开 `http://127.0.0.1:3000`，选择首页的泉州示例。示例提供只读工作台、报告版本及来源证据，可独立于后端和身份提供方运行。

```powershell
Set-Location frontend
npm ci
npm run dev -- --hostname 127.0.0.1
```

## 本地启动

需要 Node.js 20.9–20.x 或 22+、Python 3.11+、uv 和 PostgreSQL。Redis 可选，用于提前唤醒 Worker。

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
if (-not (Test-Path backend/.env)) { Copy-Item backend/.env.example backend/.env }
```

使用 Compose 时，在项目根目录的 `.env` 设置 `POSTGRES_PASSWORD`，在 `backend/.env` 的 `DATABASE_URL` 填写对应的 URL 编码密码，再运行 `docker compose up -d --wait postgres`。已有 PostgreSQL 数据卷继续使用初始化时的数据库密码。Compose 将 PostgreSQL 绑定到本机 `15432`。

启用 Redis 时运行 `docker compose up -d --wait redis`，并在 `backend/.env` 配置 `REDIS_URL=redis://127.0.0.1:16379/0`。仅使用 PostgreSQL 时可删除模板中的 `REDIS_URL` 行。

在 `backend/.env` 填入 `DEEPSEEK_API_KEY`、`BOCHA_API_KEY` 和 `FIRECRAWL_API_KEY`。Worker 启动需要 DeepSeek 密钥，默认使用官方 API `https://api.deepseek.com` 和 `deepseek-flash`。

真实调研通过 OIDC 登录。在后端进程环境或被 Git 忽略的 `backend/.env.oidc` 中设置以下值，使用提供方签发的客户端配置和实际 HTTPS 入口。

```dotenv
OIDC_ISSUER=https://id.example
OIDC_CLIENT_ID=your-client-id
OIDC_CLIENT_SECRET=your-client-secret
OIDC_PUBLIC_ORIGIN=https://travelmind.example
OIDC_TOKEN_AUTH_METHOD=client_secret_basic
```

把 `OIDC_PUBLIC_ORIGIN` 对应的 HTTPS 反向代理指向 Next.js，并在身份提供方登记回调 `https://travelmind.example/api/v1/auth/callback`。Next.js 将包括 SSE 在内的同源 `/api/v1/*` 转发至 FastAPI。代理需允许 SSE 长连接、关闭响应缓冲，并配置访问日志仅记录路径，避免记录 OIDC 回调参数。使用浏览器打开配置的 HTTPS 入口，以便发送安全会话 Cookie。

升级现有环境前，先停止旧版 FastAPI 后台执行器和旧 Worker。在后端目录安装锁定依赖、升级业务迁移并初始化 LangGraph Checkpoint，再启动 API。历史无主任务仍需由管理员显式关联到用户，详见 [Phase 2 公共契约](docs/phase-2-contract.md)。

```powershell
Set-Location backend
uv sync --locked
uv run alembic upgrade head
uv run python -m travelmind.runtime.worker_cli init-checkpoints
uv run uvicorn travelmind.main:app --host 127.0.0.1 --port 8000
```

另开一个终端，在项目根目录启动独立 Worker。可以运行多个实例。任务暂停在安全边界生效，恢复和澄清答复通过已保存的 Checkpoint 续跑。 [Worker 运行说明](docs/durable-runtime.md)

```powershell
Set-Location backend
uv run python -m travelmind.runtime.worker_cli run
```

从项目根目录再开一个终端启动 Next.js。

```powershell
Set-Location frontend
npm ci
npm run dev -- --hostname 127.0.0.1
```

Next.js 默认监听 `127.0.0.1:3000`。若后端地址不同，可将 `frontend/.env.example` 复制为 `frontend/.env.local`，在启动开发服务或运行生产构建前设置 `API_ORIGIN`。FastAPI 文档位于 `http://127.0.0.1:8000/docs`。登录工作台后可创建、暂停和恢复任务，或回答当前澄清轮次。报告获批后可从工作台打开正文。SSE 使用 AgentEvent v1，凭当前会话连接，并可通过 `Last-Event-ID` 从指定序号之后续传。

工作台和报告中心按账户将最近链接保存在当前浏览器。换设备时可打开已知链接，服务器仍校验任务归属。

### 就绪与进程监控

- `GET /health/live` 检查 API 进程。`GET /health/ready` 在 PostgreSQL 可用且任务表可访问时返回 HTTP 200，否则返回 HTTP 503。
- `redis=unavailable` 表示唤醒通知降级，`redis=not_configured` 表示使用纯 PostgreSQL 轮询。两种情况下 Worker 均继续轮询。
- 独立监控 Worker 进程及其日志。每个 Worker 启动后立即记录 `worker.alive worker_id=…`，随后每 15 秒记录一次。事件循环心跳与任务租约续期各自运行。Redis 订阅断连时 Worker 记录 `worker.redis_unavailable`。

## Research Engine

在后端的 `.env` 中设置 `BOCHA_API_KEY` 和 `FIRECRAWL_API_KEY`。OpenAlex 支持匿名查询，也可用 `OPENALEX_API_KEY` 提高额度。调用示例、错误处理及证据写入约定见 [Research Engine 接入说明](docs/research-engine.md)。

## 开发验证

- 后端目录执行 `uv run pytest` 和 `uv run ruff check .`。将 `TEST_DATABASE_URL` 指向已经执行 `alembic upgrade head` 的专用测试数据库，以运行任务状态、租约、Checkpoint 投影、OIDC 和权限集成测试。
- 前端目录执行 `npm test`、`npm run lint`、`npm run typecheck` 和 `npm run build`。测试覆盖任务控制、澄清、会话、跨账号历史隔离和 SSE 续传。
- 公共契约改动后，启动后端并在前端目录运行 `npm run generate:api`，提交生成的 TypeScript 类型。
- 如需验证 DeepSeek 密钥、网络和所选模型，从后端目录运行 `uv run python -m travelmind.runtime.check_deepseek`。该命令只读取官方模型列表。

任务状态机、API、数据模型与身份边界见 [Phase 2 公共契约](docs/phase-2-contract.md)。图状态与 Worker 事务边界见 [Agent Runtime](docs/agent-runtime.md) 和 [持久化 Worker](docs/durable-runtime.md)。

证据评分、专家反思和离线评测的共享 Schema 与接入约定见 [Phase 3 公共接口](docs/phase-3-contract.md)。
