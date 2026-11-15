# TravelMind

面向中国旅游目的地的多智能体调研平台。Next.js 工作台通过 FastAPI 启动真实调研。LangGraph 编排专家并行检索、证据评分与反思，Research Director 决定定向补查和质询。Writer 撰写报告，Reviewer 独立核验事实与引用。工作台通过 SSE 展示进度，报告可以跳转到入库的来源证据。

专家公会 `/guild` 提供九份带本地肖像的专家档案。调研工作台、事件和报告署名使用同一组身份资料。[公会与档案](docs/guild-ui.md)

## 调研质量评测

[Research Evaluation](docs/research-evaluation.md) 提供敦煌、泉州题集和离线评分入口，可用已导出的任务重算检索、证据、报告及工作流指标。用 `compare` 比较同数据集、同用例和同采集方式下的增强前后结果。[main 基线报告](docs/evaluation-baseline.md)

## 泉州示例回放

从项目根目录执行以下命令，然后打开 `http://127.0.0.1:3000`，选择首页的泉州示例。示例提供只读工作台、报告版本及来源证据，可独立于后端和身份提供方运行。

```powershell
Set-Location frontend
npm ci
npm run dev -- --hostname 127.0.0.1
```

## Windows 本地 OIDC 与 HTTPS

开发栈包含 Keycloak、Caddy、Next.js、FastAPI、PostgreSQL、Redis 和独立 Worker。使用 Docker Desktop 的 Linux 容器、Compose 2.24+、Windows PowerShell 5.1 或 PowerShell 7，以及 mkcert。可从官方发布页下载对应 Windows 架构的 mkcert，放入 PATH 或 `.artifacts/local-oidc/tools/mkcert.exe`，也可给脚本传入 `-MkcertPath`。 [mkcert 发布页](https://github.com/FiloSottile/mkcert/releases/tag/v1.4.4)

启动 Docker Desktop 后，从项目根目录运行。首次允许 mkcert 将此工作区的开发 CA 加入 Windows 信任存储。实际调研使用 `backend/.env` 中的 DeepSeek、博查和 Firecrawl 密钥，已有文件会保留。

```powershell
if (-not (Test-Path backend/.env)) { Copy-Item backend/.env.example backend/.env }
notepad backend/.env
$stack = '.\dev\local-oidc\stack.ps1'

# 仅生成开发凭据和证书。start 也会执行这一步。
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stack init
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stack start

# 显示本机随机生成的两个测试密码
Get-Content .artifacts/local-oidc/.env | Select-String '^TM_LOCAL_(ALICE|BOB)_PASSWORD='
Start-Process 'https://travelmind.localhost:18443'
```

使用 `alice` 或 `bob` 登录。要同时验证两人权限，请分别使用两个浏览器配置文件。TravelMind 的退出会吊销应用会话并关闭已建立的 SSE。Keycloak 的单点登录会话独立保存，同一浏览器配置文件再次登录可能自动选回原账号。

```powershell
$stack = '.\dev\local-oidc\stack.ps1'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stack check
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stack status
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stack restart-worker

# 停止本地容器并保留账号、任务、报告和 Checkpoint
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stack stop
```

开发 Realm 为 `travelmind-dev`，客户端为 `travelmind-local`。Issuer 是 `https://id.travelmind.localhost:18443/realms/travelmind-dev`，回调是 `https://travelmind.localhost:18443/api/v1/auth/callback`。FastAPI 和浏览器使用同一套 HTTPS Discovery、Token 和 JWKS 地址。Caddy 在应用域名直连 FastAPI 的 `/api/v1/*`，SSE 与前端同源。Caddy 只发布回环端口 `127.0.0.1:18443`，身份域名只代理开发 Realm 和登录资源。 [Keycloak 反向代理](https://www.keycloak.org/server/reverseproxy.html) · [Caddy SSE](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#streaming)

开发栈使用专用 Compose 项目和数据卷。凭据、证书与私钥位于 Git 忽略的 `.artifacts/local-oidc`。Keycloak 初次启动时导入 Realm，后续启动保留已导入的账号和客户端。外部或生产 OIDC 配置继续使用下文的独立入口。 [Keycloak Realm 导入](https://www.keycloak.org/server/importExport)

要复现浏览器验收，用 `alice` 提交目的地「古城」，说明需要先确认城市。收到澄清后答「福建泉州，仅核对宋元海贸遗产的列入年份和遗产点数量」。任务运行时暂停，等工作台显示已暂停后执行 `restart-worker`，再点恢复。报告获批后检查引用跳转。用另一个浏览器配置文件登录 `bob` 并打开相同链接，网络面板中的任务、报告与 SSE 请求应返回 404。

### Windows 故障排查

| 现象 | 检查方式 |
| --- | --- |
| Docker 不可用或不能拉取镜像 | 启动 Docker Desktop 并切换到 Linux 容器，运行 `docker info`，再执行 `start`。首次构建需访问镜像、PyPI 和 npm 仓库。 |
| 找不到 mkcert，或浏览器提示证书不可信 | 用 `-MkcertPath` 传入可执行文件路径，运行 `init`，重启浏览器并用上述域名访问。证书过期时，停止开发栈，移走 `pki/site.pem` 与 `pki/site-key.pem`，再运行 `start`。 |
| HTTPS 端口被占用 | 运行 `Get-NetTCPConnection -LocalPort 18443 -State Listen` 定位当前监听者。 |
| 登录提示回调无效或身份服务暂不可用 | 运行 `check` 检查本机信任、Discovery、JWKS 和授权跳转。用下面的日志命令定位 Caddy、Keycloak 或 FastAPI 错误。避免手工替换本地密码文件中的客户端密钥。 |
| 登录后任务长期排队 | 在 `status` 确认 Worker 运行和迁移任务正常退出，查看 Worker 日志及 `backend/.env`。修改供应商配置后重新运行 `start`。 |

```powershell
docker compose -p travelmind-local-oidc --env-file .artifacts/local-oidc/.env -f dev/local-oidc/compose.yaml logs --tail 80 caddy keycloak api migrate worker
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

升级现有环境时，在后端目录安装锁定依赖、升级业务迁移并初始化 LangGraph Checkpoint，再启动 API。启用 Phase 3 前须替换所有旧 Worker。新版 Worker 默认用 `durable-v2` 启动新任务，已有 `durable-v1` Checkpoint 按旧图恢复。历史无主任务仍需由管理员显式关联到用户。 [Phase 2 公共契约](docs/phase-2-contract.md) · [专家反思与部署](docs/expert-reflection.md)

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

持久化确认的证据先按规则筛选并排定语义评分顺序，再供专家依据分工目标判断是否补查。查看评分依据、等级及运行预算。 [Evidence Quality](docs/evidence-quality.md) · [专家反思](docs/expert-reflection.md)

## 开发验证

- 后端目录执行 `uv run pytest` 和 `uv run ruff check .`。将 `TEST_DATABASE_URL` 指向已经执行 `alembic upgrade head` 的专用测试数据库，以运行任务状态、租约、Checkpoint 投影、OIDC 和权限集成测试。
- 前端目录执行 `npm test`、`npm run lint`、`npm run typecheck` 和 `npm run build`。测试覆盖任务控制、澄清、会话、跨账号历史隔离和 SSE 续传。
- 公共契约改动后，启动后端并在前端目录运行 `npm run generate:api`，提交生成的 TypeScript 类型。
- 如需验证 DeepSeek 密钥、网络和所选模型，从后端目录运行 `uv run python -m travelmind.runtime.check_deepseek`。该命令只读取官方模型列表。

任务状态机、API、数据模型与身份边界见 [Phase 2 公共契约](docs/phase-2-contract.md)。图状态与 Worker 事务边界见 [Agent Runtime](docs/agent-runtime.md) 和 [持久化 Worker](docs/durable-runtime.md)。

证据评分、专家反思和离线评测的共享 Schema 与接入约定见 [Phase 3 公共接口](docs/phase-3-contract.md)。
