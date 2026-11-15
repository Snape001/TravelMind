# TravelMind

面向中国旅游目的地的桌面端 Web 调研平台。从一个目的地开始，确认高德 POI 后填写动态问卷。LangGraph 编排专家并行检索、证据评分与反思。三栏工作台展示研究动作、来源状态和模型实际用量。选中旅游路线后，陆知行使用高德数据规划行程，报告包含真实点位、通勤方案及证据。 [产品范围](PRD.md)

专家公会 `/guild` 提供九份带本地肖像的专家档案。调研工作台、事件和报告署名使用同一组身份资料。[公会与档案](docs/guild-ui.md)

## 调研质量评测

[Research Evaluation](docs/research-evaluation.md) 提供敦煌、泉州题集和离线评分入口，可用已导出的任务重算检索、证据、报告及工作流指标。用 `compare` 比较同数据集、同用例和同采集方式下的增强前后结果。[main 基线报告](docs/evaluation-baseline.md)

## 泉州示例回放

从项目根目录执行以下命令，然后打开 `http://127.0.0.1:3000`，打开调研历史中的泉州示例。示例提供只读工作台、报告版本及来源证据，可独立于后端和身份提供方运行。

```powershell
Set-Location frontend
npm ci
npm run dev -- --hostname 127.0.0.1
```

## Windows 本地 OIDC 与 HTTPS

开发栈包含 Keycloak、Mailpit、Caddy、Next.js、FastAPI、PostgreSQL、Redis 和独立 Worker。使用 Docker Desktop 的 Linux 容器、Compose 2.24+、Windows PowerShell 5.1 或 PowerShell 7，以及 mkcert。可从官方发布页下载对应 Windows 架构的 mkcert，放入 PATH 或 `.artifacts/local-oidc/tools/mkcert.exe`，也可给脚本传入 `-MkcertPath`。 [mkcert 发布页](https://github.com/FiloSottile/mkcert/releases/tag/v1.4.4)

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

日常执行 `start` 会复用本地前后端镜像，缺少任一应用镜像时自动构建。更新源码或依赖后用 `start -Build` 构建新版。

已有旧版开发 Realm 的机器先执行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stack upgrade-auth`，再执行 `start -Build`。升级脚本原地启用账户功能并保留原有用户。空数据卷首次启动时自动导入 Realm。

可以使用 `alice` 或 `bob` 登录，也可以用测试邮箱注册。注册和密码重置邮件由 Keycloak 通过本地 SMTP 发送到 [Mailpit 收件箱](http://127.0.0.1:18025)。邮箱验证后设置密码并登录，资料页可修改昵称。账户菜单退出时先吊销 TravelMind 会话，再进入 Keycloak 单点退出，完成后可重新登录。使用独立浏览器配置文件验证多设备及跨用户权限。 [SMTP、Realm 导入和生产配置](docs/account-auth-deployment.md)

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

要复现浏览器验收，用 `alice` 提交「泉州大开元寺」，核对 POI 和行政区，勾选「旅游路线」并按需填写出行偏好。任务运行时暂停，等工作台显示已暂停后执行 `restart-worker`，再点恢复。查看 Research Browser、专家用量和已审核报告地图。用另一个浏览器配置文件登录 `bob` 并打开相同任务，任务、来源、地图、报告和两条 SSE 请求应返回 404。

### Windows 故障排查

| 现象 | 检查方式 |
| --- | --- |
| Docker 不可用或构建时报仓库连接超时 | 确认 Docker Desktop 正在运行 Linux 容器。已有应用镜像时用 `start` 启动。缺少镜像或执行 `start -Build` 时需能访问容器镜像仓库、PyPI 和 npm 仓库。 |
| 找不到 mkcert，或浏览器提示证书不可信 | 用 `-MkcertPath` 传入可执行文件路径，运行 `init`，重启浏览器并用上述域名访问。证书过期时，停止开发栈，移走 `pki/site.pem` 与 `pki/site-key.pem`，再运行 `start`。 |
| HTTPS 端口被占用 | 运行 `Get-NetTCPConnection -LocalPort 18443 -State Listen` 定位当前监听者。 |
| 登录提示回调无效或身份服务暂不可用 | 运行 `check` 检查本机信任、Discovery、JWKS 和授权跳转。用下面的日志命令定位 Caddy、Keycloak 或 FastAPI 错误。避免手工替换本地密码文件中的客户端密钥。 |
| 没有注册入口，或收不到验证和重置邮件 | 已有 Realm 先执行 `upgrade-auth`，再执行 `start` 和 `check`。确认 Mailpit 监听本机 18025，查看 Keycloak 和 Mailpit 日志。 |
| 登录后任务长期排队 | 在 `status` 确认 Worker 运行和迁移任务正常退出，查看 Worker 日志及 `backend/.env`。修改供应商配置后重新运行 `start`。 |

```powershell
docker compose -p travelmind-local-oidc --env-file .artifacts/local-oidc/.env -f dev/local-oidc/compose.yaml logs --tail 80 caddy keycloak mailpit api migrate worker
```

## 本地启动

需要 Node.js 20.9–20.x 或 22+、Python 3.11+、uv 和 PostgreSQL。Redis 可选，用于提前唤醒 Worker。

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
if (-not (Test-Path backend/.env)) { Copy-Item backend/.env.example backend/.env }
```

使用 Compose 时，在项目根目录的 `.env` 设置 `POSTGRES_PASSWORD`，在 `backend/.env` 的 `DATABASE_URL` 填写对应的 URL 编码密码，再运行 `docker compose up -d --wait postgres`。已有 PostgreSQL 数据卷继续使用初始化时的数据库密码。Compose 将 PostgreSQL 绑定到本机 `15432`。

启用 Redis 时运行 `docker compose up -d --wait redis`，并在 `backend/.env` 配置 `REDIS_URL=redis://127.0.0.1:16379/0`。仅使用 PostgreSQL 时可删除模板中的 `REDIS_URL` 行。

在 `backend/.env` 填入 `DEEPSEEK_API_KEY`、`BOCHA_API_KEY` 和 `FIRECRAWL_API_KEY`。Worker 启动需要 DeepSeek 密钥，默认使用官方 API `https://api.deepseek.com` 和 `deepseek-flash`。问卷、研究、评分和报告共用 `DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 及同一模型封装。覆盖变量后重启 Worker。

真实调研通过 OIDC 登录。在后端进程环境或被 Git 忽略的 `backend/.env.oidc` 中设置以下值，使用提供方签发的客户端配置和实际 HTTPS 入口。

```dotenv
OIDC_ISSUER=https://id.example
OIDC_CLIENT_ID=your-client-id
OIDC_CLIENT_SECRET=your-client-secret
OIDC_PUBLIC_ORIGIN=https://travelmind.example
OIDC_TOKEN_AUTH_METHOD=client_secret_basic
```

把 `OIDC_PUBLIC_ORIGIN` 对应的 HTTPS 反向代理指向 Next.js，并在身份提供方登记回调 `https://travelmind.example/api/v1/auth/callback`。Next.js 将包括 SSE 在内的同源 `/api/v1/*` 转发至 FastAPI。代理需允许 SSE 长连接、关闭响应缓冲，并配置访问日志仅记录路径，避免记录 OIDC 回调参数。使用浏览器打开配置的 HTTPS 入口，以便发送安全会话 Cookie。

使用 Keycloak 账户模式时，完成 SMTP、邮箱验证、精确的退出返回地址和后通道配置，应用迁移 `0007` 后再设置 `OIDC_ACCOUNT_ENABLED=true`。通用 OIDC 可继续使用应用本地注销。 [账户部署步骤](docs/account-auth-deployment.md)

升级现有环境时，在后端目录安装锁定依赖、升级业务迁移并初始化 LangGraph Checkpoint，再启动 API。启用 Phase 3 前须替换所有旧 Worker。新版 Worker 默认用 `durable-v2` 启动新任务，已有 `durable-v1` Checkpoint 按旧图恢复。历史无主任务仍需由管理员显式关联到用户。 [Phase 2 公共契约](docs/phase-2-contract.md) · [专家反思与部署](docs/agent-runtime.md#专家反思)

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

工作台的最近任务链接保存在当前浏览器。「我的报告」由 PostgreSQL 按会话所有权分页，同一账户可跨设备查看已发布报告和访问自己的历史链接。

### 就绪与进程监控

- `GET /health/live` 检查 API 进程。`GET /health/ready` 在 PostgreSQL 可用且任务表可访问时返回 HTTP 200，否则返回 HTTP 503。
- `redis=unavailable` 表示唤醒通知降级，`redis=not_configured` 表示使用纯 PostgreSQL 轮询。两种情况下 Worker 均继续轮询。
- 独立监控 Worker 进程及其日志。每个 Worker 启动后立即记录 `worker.alive worker_id=…`，随后每 15 秒记录一次。事件循环心跳与任务租约续期各自运行。Redis 订阅断连时 Worker 记录 `worker.redis_unavailable`。

## Research Engine

在后端的 `.env` 中设置 `BOCHA_API_KEY` 和 `FIRECRAWL_API_KEY`。OpenAlex 支持匿名查询，也可用 `OPENALEX_API_KEY` 提高额度。调用示例、错误处理及证据写入约定见 [Research Engine 接入说明](docs/research-engine.md)。

入库证据先经规则筛选，再由专家结合评分和任务目标判断是否补查。 [Evidence Quality](docs/evidence-quality.md) · [专家反思与预算](docs/agent-runtime.md#专家反思)

### 游记与地方志

非商业学习与研究可在 `backend/.env` 设置 `CNKGRAPH_ENABLED=true`。本机运行时重启 Worker，使用 Docker OIDC 开发栈时重新执行 `stack.ps1 start` 以载入配置。沈观澜在深度或学术调研中查询古籍文献知识图谱网，优先查游记、方志和历史地理文献，并引用可定位的正文摘录。无需账号或 API Key。可用「游天台山日记」或「泉州府 城池」验证。 [来源与使用范围](https://api.cnkgraph.com/Home/OpenResources) · [接口与证据细节](docs/research-engine.md#游记与地方志)

## 高德地图配置

在[高德开放平台控制台](https://console.amap.com/)创建应用并分别申请两种 Key。添加 Key 时选择「Web 服务」，供后端的 POI、输入提示、地理编码及路径规划使用。另添加「Web 端（JS API）」Key，取得配套安全密钥，并把本地和生产站点域名加入该 Key 的白名单。使用 Windows OIDC 开发栈时，本地站点为 `travelmind.localhost`。 [申请 Web 服务 Key](https://lbs.amap.com/api/webservice/create-project-and-key) · [JS API 安全密钥](https://lbs.amap.com/api/javascript-api-v2/guide/abc/jscode)

将以下值填到 `backend/.env`。首次配置可复制 `backend/.env.example`。文件已存在时，追加相应的三行并保留原有配置。

```ini
AMAP_WEB_API_KEY=
NEXT_PUBLIC_AMAP_JS_API_KEY=
AMAP_SECURITY_JS_CODE=
```

- `AMAP_WEB_API_KEY` 由后端和 Worker 读取。
- 林景川在景点资源调研中复用该 Key 查询景点详情，并将高德返回的营业时间、电话和出入口作为可引用的地图证据。 [查询范围和缺失字段处理](docs/research-engine.md#高德景点资料)
- Next.js 同源转发 `/_AMapService`。地图 SDK 加载器从已登录的服务端配置接口读取公开 JS Key，服务端代理向固定高德上游注入 `AMAP_SECURITY_JS_CODE`。修改值后重启 API 和 Worker 即可，JS Key 在运行时读取。两个敏感值只留在后端。
- `.env` 与 `.env.local` 已被 Git 忽略。请只提交 `.env.example` 模板。

填写 Web 服务 Key 后，从仓库根目录运行以下命令。脚本从实际高德返回的两个北京 POI 获取坐标，另外请求目的地输入提示及驾车路线。输出每项状态和安全错误码。退出码 `0` 表示三项通过，`1` 表示请求或结果校验失败，`2` 表示配置缺失或无效。

```powershell
Set-Location backend
& .\.venv\Scripts\python.exe -m travelmind.research.check_amap
```

该脚本可独立验证 Web 服务凭据。结构化问卷选中旅游路线后，默认 Worker 调度陆知行，并随已审核报告发布地图数据。 [Schema 与查询接口](docs/phase-4-travel-planning.md#地图查询)

### Docker 更新 API 与 Worker

从项目根目录执行 `start` 可启动已有镜像并运行数据库迁移。要把源码或依赖的变更部署到本地 OIDC 栈，运行以下命令。

```powershell
.\dev\local-oidc\stack.ps1 start -Build
```

如果本地栈正在运行，可以单独升级后端。先停旧 Worker，重建后端并迁移，再重建 API 和 Worker 容器，让 Docker Compose 重新读取 `backend/.env`。

```powershell
$travelCompose = @('--env-file', '.artifacts/local-oidc/.env', '-f', 'dev/local-oidc/compose.yaml')
docker compose @travelCompose stop worker
docker compose @travelCompose build migrate
docker compose @travelCompose run --rm --no-deps migrate
docker compose @travelCompose up -d --no-deps --force-recreate api worker
.\dev\local-oidc\stack.ps1 check
```

`api` 和 `worker` 共用 `backend/.env`。`frontend` 通过同源代理读取公开 JS Key。只改密钥时直接重建相应容器即可。

## 开发验证

- 后端目录执行 `uv run --locked pytest --basetemp ../.artifacts/tests/backend/pytest --junitxml ../.artifacts/tests/backend/junit.xml` 和 `uv run --locked ruff check .`。将 `TEST_DATABASE_URL` 指向已经执行 `alembic upgrade head` 的专用测试数据库，以运行任务状态、租约、Checkpoint 投影、OIDC 和权限集成测试。
- 前端目录执行 `npm test`、`npm run lint`、`npm run typecheck` 和 `npm run build`。测试覆盖任务控制、澄清、会话、报告分页和 SSE 续传。真实浏览器用例及合成报告装载说明见 [账户认证验收](docs/account-auth-qa.md)。
- 测试执行产物统一写入仓库根目录的 `.artifacts/tests/`。前端编译文件位于 `frontend/` 子目录，由 `npm test` 重新生成。后端和其他验收报告各用自己的子目录。 [测试产物约定](DEVELOPMENT_GUIDELINES.md#3-测试先行test-driven-development)
- 公共契约改动后，启动后端并在前端目录运行 `npm run generate:api`，提交生成的 TypeScript 类型。
- 如需验证 DeepSeek 密钥、网络和所选模型，从后端目录运行 `uv run python -m travelmind.runtime.check_deepseek`。该命令只读取官方模型列表。

任务状态机、API、数据模型与身份边界见 [Phase 2 公共契约](docs/phase-2-contract.md)。图状态与 Worker 事务边界见 [Agent Runtime](docs/agent-runtime.md) 和 [持久化 Worker](docs/durable-runtime.md)。

证据评分、专家反思和离线评测的共享 Schema 与接入约定见 [Phase 3 公共接口](docs/phase-3-contract.md)。

Phase 4 新问卷任务使用带 Skills 的 `durable-v4`。迁移 `0004` 至 `0006` 持久化问卷、报告地图、来源和扩展事件。已有任务按原 Checkpoint 版本恢复。 [产品范围](PRD.md#phase-4) · [公共接口](docs/phase-4-contract.md) · [架构与验收](docs/phase-4-architecture.md) · [桌面工作台](docs/phase-4-desktop-ui.md)

苏听雨可从本机授权缓存读取知乎回答、小红书笔记和 B 站评论，报告会保留可引用的来源和样本统计。Docker OIDC 开发栈使用 `stack.ps1 start -SocialCachePath <授权缓存路径>` 启用。在调研问卷中选择「游客体验与口碑」即可安排苏听雨。 [启用步骤与样本范围](docs/phase-4-social-research.md#worker-配置与样本)

知乎开放平台采集支持站内问题和回答摘要、问题下的回答列表，以及本人回答详情和接口附带的评论回复。首次调用前使用 `ZhihuQuota` 验证凭据和查看限免额度。 [开放平台权限、配置与收费](docs/phase-4-social-research.md#开放平台采集)
