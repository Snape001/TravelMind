# TravelMind 开发进度

## Phase 4 · C 线专家 Skills 与报告

- 交付版本化技能定义、专家档案和按角色及阶段加载的注册表。Writer 依序执行大纲、跨专家证据整合、撰写和全篇编辑，独立 Reviewer 核对事实、引证、覆盖和旅游实用性。沿用现有报告及事件契约。 [技能与集成说明](docs/expert-skills-report.md)
- 使用独立 PostgreSQL 14 执行后端回归，227 项测试通过，含 19 项新增专项用例。Ruff、Alembic 一致性检查和 Checkpoint 初始化通过。泉州公开资料样例真实调用 `deepseek-flash` 五次，产出两章及一条引用，独立 Reviewer 批准。
- 下一步由 Phase 4 集成工作流启用技能入口，接入正式专家调度、问卷和路线资料。本提交保留旧图及默认 Worker 路径。
- **交付提交**　`feat: add expert skills and staged report writing`。

## Phase 4 · Step 0 公共契约

- 冻结目的地、动态问卷、路线偏好、专家档案与 Skills、Research Browser 事件、模型用量和社交证据。交付 Pydantic Schema、共享合成 Fixture 和独立类型导出。现有路由、AgentEvent v1、六态任务机及 Worker 仍走 Phase 3。 [公共接口](docs/phase-4-contract.md)
- 确认高德 POI、行政区划和 GCJ-02 坐标映射，保留 `travel_planning` Agent ID 给陆知行。浏览器扩展使用独立 SSE 地址及游标，沿用任务归属和会话复查。真实路线、供应商 Usage 和授权社交样本将在各自功能线验收。
- 后续从本提交创建独立 Worktree。A 问卷、C Skills 与报告、D 桌面 UI、E 社交 PoC 并行，B 依赖已冻结的目的地模型，联调时复用 A 的高德客户端。按 A → B → C → D → E 集成，再进行真实浏览器和外部服务验收。高德凭据及 MediaCrawler 许可与授权要求见 [架构和验收标准](docs/phase-4-architecture.md)。
- PostgreSQL 17 上 208 项后端测试通过，其中 26 项为新增契约用例。27 项前端测试、Ruff、ESLint、TypeScript 和 Alembic 一致性通过。独立 Phase 4 类型生成后通过 TypeScript 检查，现有 FastAPI OpenAPI 重新生成的前端类型与仓库一致。
- **交付提交**　`feat: freeze Phase 4 public contracts`。

## Phase 3 · 发布验收（2026-11-15）

- 删除对旧提交有硬性依赖的基线采集脚本，保留可重算的候选快照、独立判读、基线报告和复现测试。离线评测与 Runtime 共用反思轮数及独立来源判定，补充同域名不同网页的冲突回归。
- 删除重复的专家反思文档、前端样式覆盖、测试包装和本地 Compose 环境变量覆写。更新产品及开发说明，确认桌面端 Web 范围，现有检索预算和数据源沿用至本版。 [产品范围](PRD.md) · [运行与升级](docs/agent-runtime.md) · [评测复现](docs/research-evaluation.md)
- 临时 PostgreSQL 17 从空库迁移到最新版本，182 项后端测试通过，含 Worker 租约与 Checkpoint 恢复、OIDC、CSRF 和用户隔离。Checkpoint 初始化及 Alembic 一致性检查通过。前端 27 项通过，OpenAPI 类型重新生成后与仓库一致。Ruff、ESLint、TypeScript、Next.js 生产构建、基础 Compose、本地 OIDC Compose 和 Caddy 配置通过。
- Python 锁文件和实际安装校验通过。Python 与 npm 依赖审计未报告已知漏洞，当前 Windows 安装中的两个多余 npm 包已清理。Git 扫描覆盖本地配置值、常见凭据格式和敏感文件路径。
- 本次发布回归使用隔离数据库、自动化测试和既有真实调研验收记录。离线评测题集仍待人工签核。Research Browser、Agent Hover 指标和研究监控可视化归入 Phase 4。
- **发布标签**　`v0.3.0`。**交付提交**　`refactor: clean up TravelMind Phase 3 for release`。

## 本地 OIDC 与 HTTPS 验收（2026-11-15）

- 加入独立的 Keycloak 与 Caddy 开发栈。mkcert 使用工作区专属 CA，Caddy 只监听回环 HTTPS。在 `travelmind-dev` 导入两位开发用户和要求 PKCE S256 的授权码客户端。浏览器与 FastAPI 共用 HTTPS Issuer，开发凭据和证书保存在 Git 忽略目录。 [Windows 启停与排查](README.md#windows-本地-oidc-与-https)
- 两个独立 Chrome 会话完成真实 Keycloak 登录，校验应用 Cookie 的 Secure、HttpOnly、SameSite=Lax 和主机范围。Alice 未提交 CSRF 令牌的写入返回 403。已连接的独立 SSE 在 Alice 退出后由服务器关闭，旧会话凭据返回 401。Bob 读取 Alice 的任务、报告和 SSE 均返回 404。
- 浏览器提交真实调研，回答 Director 对「古城」的追问，暂停后重启 Worker 并继续。最多三位专家并行，共收到 61 条 SSE 事件。报告获批，证据跳转成功。Windows PowerShell 完整执行停启后，测试凭据和任务仍在。
- 容器生产构建、Caddy 配置、Discovery/JWKS、后端 181 项、前端 27 项、Ruff、Alembic、ESLint 和 TypeScript 检查通过。当前验收涵盖本地提供方，生产域名及外部 OIDC 独立配置。
- **交付提交**　`feat: add local Keycloak HTTPS development stack`。

## Phase 3 · 最终集成（2026-11-15）

| 顺序 | 实际来源 | 原始提交 | 合并提交 |
| --- | --- | --- | --- |
| 1 | `codex/evidence-quality` | `8b90fa3` | `d5289c9` |
| 2 | `codex/expert-reflection` | `5e8cf09` | `d7b9bea` |
| 3 | 原本已在本地 `main` 的 Research Evaluation | `cf05c51` | `0b10e44` |
| 4 | `codex/guild-ui-optimization` | `50db36b` | `5b855a6` |

- 集成冲突均在本进度文件，各分支的验收记录已保留。Research Engine 保存证据后按质量筛选排序和评分。新版 Worker 用 `durable-v2` 运行专家反思，总监按分工协调定向补查和质询，`durable-v1` 继续按原图恢复。Writer 聚焦已核实结论及重要缺口，Reviewer 独立核对原文、署名和引用分级。[Runtime](docs/agent-runtime.md) · [评分契约](docs/evidence-quality.md)
- 专家事件按实际执行顺序投影，复用 AgentEvent v1。公会、工作台及报告共用专家档案，真实 Writer 事件使用本地肖像及档案链接。FastAPI OpenAPI 重新生成的前端类型与仓库一致。专用 PostgreSQL 14 上 181 项后端测试全部通过，27 项前端测试全部通过。Ruff、Alembic 一致性、锁文件、ESLint、TypeScript 与 Next.js 生产构建均通过。
- 泉州遗产题在真实 DeepSeek、博查、Firecrawl 环境下通过生产 Next.js 代理提交，并由 Worker 以 PostgreSQL 领取。旧图完成 3 章，54 条事件。新版首轮真实验收因草稿被退回而失败。修正评分批次保留有效结果、限制调研和撰写范围后，新图在一次返工后通过独立审核，发布 2 章报告、10 条已存证据、19 张评分卡和 56 条 SSE 事件，从中间游标重放 28 条。四个分工各完成一轮补查。Chrome 使用同一真实任务记录重放，验证工作台、Writer 肖像、报告跳转、引用锚点和公会九张本地肖像。
- 同题配对仅独立判读「列入年份」和「遗产点数量」。两版本均覆盖 2/2 个参考发现，引用可解析 10/10 和 13/13。模型调用 16 → 31，耗时约 35.8 → 57.8 秒。新版将年份标注为间接支撑，仍缺 UNESCO 原始档案。该样本用于诊断，尚需对 36 题人工签核并重复采样。实际运行暂无可比检索排名，未做全文忠实度判断。[配对评测](docs/research-evaluation.md#比较增强前后)
- **联调提交**　`0b58001`、`bf70de7`、`bb9cf34`。

## Phase 3 · Evidence Quality（2026-11-15）

- 实现六类来源的 0–100 加权筛选。返回权威性、时效、相关性、完整性和独立性依据，以及 A/B/C/D 对应的使用、交叉核实、补查或排除动作。原始 Evidence ID、来源与摘录随结果保留。[接入与权重说明](docs/evidence-quality.md)
- 优先使用确定性规则，可接入按 Evidence ID 提供的归一化检索分数。按需批量调用既有结构化模型，逐字校验摘录并缓存有效判断。完整评分卡沿用冻结的等权 `EvidenceQuality` v1。启用语义评分和调度后续检索由调用方接入。
- 临时 PostgreSQL 14 从空库迁移到 `0003` 后通过 146 项后端测试，其中 28 项为新增评分用例。Ruff 与 Alembic 一致性检查通过。可选模型接入通过测试替身验证批量、校验和缓存行为。
- **交付提交**　`codex/evidence-quality` 基于 `aedb2b7`。提交 `feat: implement evidence quality screening`。

## Phase 3 · Expert Reflection（2026-11-15）

- 各专家独立判断证据覆盖、评级、独立佐证与关键冲突。Research Director 负责分工补查、轮次与预算、全任务最多一次的定向质询。终态保存结构化判断与未解决问题，Writer 和 Reviewer 延续独立职责。 [运行说明](docs/agent-runtime.md#专家反思)
- Worker 在可用时对接 A 线评分器，启动 `durable-v2`。已保存的 `durable-v1` 任务按旧图续跑。已验证暂停后继续、已提交轮次恢复、仅投影租约补齐结果和旧 epoch 拒写。
- 临时 PostgreSQL 14 上独立分支 138 项通过，1 项联调随 A 线提供。接入 A 线提交 `8b90fa3` 后 167 项通过，含真实评分器代码及固定语义响应。Ruff 通过，外部模型和检索供应商留待真实任务验收。
- **交付提交**　`feat: add bounded expert reflection workflow`。

## Phase 3 · Research Evaluation（2026-11-15）

- 交付独立离线评分 CLI 和敦煌、泉州各 18 题。每题含参考发现与原始来源定位，36 题待人工审核。Gold 需显式人工签核。评分复用公共 Schema，覆盖检索、证据、报告、Agent 事件与运行统计。
- 以本地 `main` 的 `aedb2b7` 采集三个固定输入工作流，另复用一份仓库泉州演示。回放成功 2/3，包含一次审核返工和一次预设检索超时。运行时未调用真实模型。基线保留候选快照和独立判读，可重复生成 JSON 与 Markdown。[操作说明](docs/research-evaluation.md) · [基线报告](docs/evaluation-baseline.md)
- 在临时 PostgreSQL 14 上全部 127 项后端测试通过，含 9 项评测测试。前端 22 项通过。Ruff、Alembic 一致性、锁文件检查及生成报告一致性均通过。
- 待人工签核首批题集，并接入真实调研快照、检索排名和 Token 观测；本批回放覆盖 3 道题。
- **交付提交**　`feat: add offline research evaluation and main baseline`。

## Phase 3 · 公共接口基线（2026-11-15）

- 冻结 `EvidenceQuality`、`ReflectionState`、`ReflectionAction`、`ExpertResearchResult` 和 `EvaluationCase`。评分附规则依据或原文摘录，绑定证据、分工、评分版本和时间上下文。反思契约定义四种状态、补查策略和停止条件，离线评测以参考发现及证据快照为基线。[架构及接口](docs/phase-3-contract.md)
- 在迁移后的临时 PostgreSQL 14 上通过全部 118 项后端测试，其中 30 项为新增契约用例。前端 22 项测试、Ruff、锁文件与 Alembic 一致性检查均通过。FastAPI 测试客户端读取 OpenAPI 返回 HTTP 200，重新生成的前端 API 类型与仓库一致。
- 后续接入评分执行、专家反思和离线评分，验证存量 Checkpoint 恢复及真实模型输出。当前交付为共享 Schema 和接入约定。
- **交付提交**　`feat: freeze Phase 3 research quality contracts`。

## Guild UI Optimization · 前端支线（2026-11-15）

- 公会按领导层、专业专家层和执行层展示九位专家，统一档案字段并提供详情页。九张肖像由内置 imagegen 生成，按原有 Agent ID 保存为本地 WebP。历史任务中的旅游规划角色仍可打开原档案。
- 工作台的事件、组织节点和任务分工共享专家身份展示。报告用现有章节署名显示头像和角色，标示明示的专家共识及争议章节，并保留目录和证据跳转。后端 API、LangGraph Runtime、AgentEvent v1 和工具调度经差异检查无变更。
- **验收**　26 项前端测试通过，ESLint、TypeScript 和 Next.js 生产构建通过。1024 和 1440 像素桌面浏览器验证公会布局、档案、专家筛选和报告引用。本地 HTTP 检查九份档案及肖像、历史档案和未知档案 404。用契约 Fixture 验证登录、SSE 在第 9 条事件断开后续传至 39 条事件、报告及证据读取、退出后隐藏受限报告。
- **遗留与下一步**　专家档案仍使用前端 Fixture，后续接入统一档案配置。真实服务未在本支线重新联调。实现与肖像规范见 [Guild UI Optimization](docs/guild-ui.md)。
- **交付提交**　基于 `main@350acf1` 的 `codex/guild-ui-optimization`，提交 `feat: redesign the expert guild experience`。

## Phase 2 · 正式完成与发布验收（2026-11-15）

- 删除 Phase 1 内嵌执行器和 Worker 构造器的重复预算配置。原执行器的集成断言迁入真实 PostgreSQL 与 Checkpoint 测试。移除公开模拟任务和登录路由、运行时模拟开关及重复文档，保留测试 Fixture 和只读泉州示例。
- Worker 复用并释放模型客户端。外部材料、澄清答复、专家成果和审核意见通过独立 JSON 消息传给模型，输出继续经 Schema 验证和来源核对。检索限制供应商响应大小，在一个租约中合并同源抓取，并由租约保护证据落库。仅投影租约、旧 epoch 拒写和已保存中断恢复均经回归验证。
- 停止记录 OIDC 回调查询参数和 HTTP 客户端的完整请求 URL，隐藏数据库参数。Compose 凭据改由本地环境注入。Alembic 识别 LangGraph 独立管理的 Checkpoint 表，业务迁移校验通过。AgentEvent v1 沿用冻结契约，重新生成的 API 类型无差异。
- 专用 PostgreSQL 上后端 88 项、前端 22 项全部通过，无跳过。Ruff、ESLint、TypeScript、Next.js 生产构建、Docker Compose 配置和锁定依赖检查均通过。移除 Tailwind 工具链，升级 ESLint 链中的 `minimatch` 到 `11.0.7`，本次 npm 审计与 Python 锁文件审计均为 0 项已知漏洞。
- Chrome 验证示例报告、引用，以及经 Next.js 转发的 SSE 重连、去重、临时 503 恢复和登出断流。真实博查、OpenAlex 和 Firecrawl 均返回证据。真实调研在明确章节署名规则后通过审核，写入 5 章节报告和 59 条事件，Redis 断连期间仍由 Worker 轮询领取。此前两次真实调研按 `REVIEW_REJECTED` 正常结算。
- 正式 OIDC 提供方与生产 HTTPS 域名仍待部署验证。任务历史由当前浏览器按账户保存。非可信内容隔离与结构化校验降低提示注入风险，模型仍可能受材料中的误导内容影响。
- **交付提交**　`refactor: harden and clean up Phase 2`。

## Phase 2 · 最终集成（2026-11-15）

| 顺序 | 功能分支 | 分支头 | 合并提交 |
| --- | --- | --- | --- |
| 1 | `codex/task-service-auth`（含重试预算修复） | `ad337ec` | `0746fd6` |
| 2 | `codex/durable-runtime`（含预算执行许可及 PyJWT 依赖修复） | `f9eda46` | `b0067ef` |
| 3 | `codex/frontend-phase2` | `0e2f0a6` | `ecfd4da` |

- **集成**　保留三个分支的原始提交。沿用 AgentEvent v1，从集成版 API 重新生成前端 OpenAPI 类型，内容一致。PostgreSQL 决定 API 就绪，Redis 故障显示为降级，Worker 继续轮询 PostgreSQL，并单独按实例记录存活心跳。
- **自动化验收**　全新环境从 `uv.lock` 安装 `PyJWT[crypto]` 后，OIDC 正常启动并验证 RS256 签名。专用 PostgreSQL 从空库迁移到 `0003`，后端 78 项、前端 21 项测试全部通过。预算用例覆盖 `max_attempts=1` 下的多次过期、已完成 Checkpoint 的投影补齐、未完成的图停止推进、Worker 竞争、旧 epoch 拒写。Ruff、Alembic 一致性、锁定依赖检查、ESLint、TypeScript 和 Next.js 生产构建全部通过。
- **浏览器联调**　本地测试 OIDC 提供方通过 HTTPS 签发 ID Token，双账户验证登录、CSRF、任务所有权及 SSE 隔离。固定模型和检索响应的用例验证了暂停、恢复、Worker 重启后答复澄清、报告读取、48 条 SSE 续传事件以及已连接 SSE 在登出后关闭。实测 Redis 断连时就绪返回 200 并降级为轮询，PostgreSQL 断连时返回 503。
- **外部服务实测**　在 Redis 断连的验收环境，Chrome 提交泉州世界遗产调研，独立 Worker 通过 PostgreSQL 轮询，调用真实 DeepSeek、博查和 Firecrawl。报告获批，含 5 个章节，并从指定游标回放 53 条 SSE 事件。前两轮真实调研在一次返工后仍被审核拒绝，按 `REVIEW_REJECTED` 结算。
- **部署验证**　业务迁移与 Checkpoint 初始化步骤已列入 [README](README.md#本地启动)。正式环境的 OIDC 提供方与 HTTPS 域名仍待部署联调。
- **集成收尾提交**　`fix: keep API ready when Redis is unavailable`。

## Phase 2 · 公共契约基线

- **重试预算修订**　[公共契约](docs/phase-2-contract.md#重试预算与领取结果) 定义执行租约的计数、过期回收、仅投影模式和耗尽结算。新增验收用例待 Task Service 与 Runtime 实现后执行。
- **完成情况**　冻结六种任务状态、暂停与恢复、澄清答复、错误和幂等规则，补充 `TaskRead`、澄清与会话 Schema，并定义带租约校验的异步 `WorkerStore` 协议。数据库字段、迁移责任与任务所有权约定见 [公共契约基线](docs/phase-2-contract.md)。
- **验收结果**　后端 42 项测试通过，含基于 Alembic 初始化的专用 PostgreSQL 集成测试。前端 10 项测试及 TypeScript 检查通过。Ruff、Python 锁文件和 Alembic 一致性检查通过。FastAPI 测试客户端验证服务启动、OpenAPI 兼容性及已有任务接口。
- **下一步**　任务服务实现新增路由、OIDC 和业务迁移，Runtime 实现租约存储对接与 Checkpoint 续跑，Frontend 接入控制与认证流程并生成最终 API 类型。AgentEvent v1 沿用现有线协议。
- **交付提交**　`feat: freeze Phase 2 public task contracts`。

## Phase 2 · 任务服务与身份认证

- **任务控制**　暂停、恢复和澄清答复已接入现有任务路由。控制命令在持有任务行锁时提交状态、事件与幂等响应快照。创建幂等键按用户隔离。任务可领取后提交事务，再发 Redis 唤醒信号。Redis 不可用时由 Worker 轮询领取。
- **Worker 交接**　`travelmind.db.worker.PostgresWorkerStore(sessions)` 实现冻结的异步存储协议。Checkpoint 和证据写入分别传入已开启事务的 psycopg 连接和 SQLAlchemy 会话执行租约校验。回收者应先对账已持久化的 Checkpoint，再决定是否继续执行或通过 `AttemptFailure` 结算故障。默认最多 3 次尝试，故障结算按预算重排或进入终态。
- **迁移**　停止旧 FastAPI 后台执行器后执行 `alembic upgrade head`（业务版本 `0002`）。旧任务缺省无主，历史已启动且无 Checkpoint 的未完成任务会追加失败事件。未启动的历史任务经管理员显式映射用户后，可设置 `available_at` 加入队列。
- **身份配置**　在后端环境变量或被 Git 忽略的 `backend/.env.oidc` 中设置 `OIDC_ISSUER`、`OIDC_CLIENT_ID`、`OIDC_CLIENT_SECRET` 和 `OIDC_PUBLIC_ORIGIN`。提供方应允许 `{OIDC_PUBLIC_ORIGIN}/api/v1/auth/callback`，浏览器使用同源 HTTPS。可通过 `OIDC_TOKEN_AUTH_METHOD` 选择 `client_secret_basic`（默认）、`client_secret_post` 或公开客户端的 `none`，通过 `OIDC_SESSION_SECONDS` 设置会话期限。后端 OIDC 验签依赖 `PyJWT[crypto]>=2.10,<3`，由 Runtime 分支统一锁定。最终 API 类型可由任务服务的 `/openapi.json` 生成，SSE 使用原 AgentEvent v1。
- **验收**　专用 PostgreSQL 上 53 项后端测试全部通过，Ruff 和 Alembic 一致性检查通过。实测含历史数据的 `0001 → 0002` 迁移、回滚后升回 `0002`，以及 psycopg 事务中的任务行锁和旧租约拒写。OIDC 由本地测试提供方返回实际签名的 ID Token，覆盖 PKCE、签名、签发者、受众、过期、nonce、Cookie、CSRF、所有权和 SSE 会话吊销。
- **待联调**　通过实际 OIDC 提供方和 HTTPS 代理完成浏览器登录，并与独立 Worker 验证 Checkpoint 跨进程续跑。
- **交付提交**　基于 `48df38b` 的 `codex/task-service-auth`，提交 `feat: implement durable task service and OIDC sessions`。冻结契约、公共 Schema、Runtime 和前端文件经差异检查。

### Worker 重试预算修订（契约 `508775c`）

- `0003` 为任务持久化 `max_attempts`（默认 3）及各 epoch 的执行许可。执行租约在任务行锁内递增计数。过期回收使用数据库时钟执行退避，预算耗尽时只签发可补投影的租约。仅投影租约可以续租、读取指令、提交已持久化的结果或结算失败。Checkpoint 和证据写入仍要求执行许可。
- 迁移将旧租约到期，保留数据库中的任务和投影。旧版本已排队且计数达到默认上限的任务保留一笔已排队尝试，其他超限旧计数归至上限。部署前需停止旧 Worker，完成 `alembic upgrade head` 后启动匹配新契约的 Runtime。
- 专用 PostgreSQL 上 56 项任务服务回归通过，另有 18 项 Runtime 联调通过，其中真实 Checkpointer 用例覆盖终态报告补投影、旧 epoch 写入受阻和跨连接暂停及澄清续跑。Ruff、Alembic 一致性和带历史数据的 `0002 → 0003` 迁移验证通过。
- **交付提交**　`fix: enforce durable worker attempt budgets in PostgreSQL`。

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
