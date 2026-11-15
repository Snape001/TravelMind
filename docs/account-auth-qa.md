# TravelMind 账户认证升级验收计划

本计划从冻结的账户认证契约 `476450d` 派生。用例、回归入口和 Fixture 可在本地重复运行。最终集成的实测结果见本页末尾。 [账户认证契约](account-auth-contract.md)

## 环境与 Fixture

使用专用本地验收栈，包含固定版本 Keycloak 26.8.2、受信 HTTPS、FastAPI、Next.js、已迁移的 PostgreSQL 和只绑定回环的 SMTP 捕获器。启用 `OIDC_ACCOUNT_ENABLED`，记录候选提交、迁移版本、Realm/Client 导入或升级方式、邮件链接 TTL、应用会话 TTL 及 Keycloak SSO／Client Session TTL。用干净业务库精确比对分页预期，另外准备旧版 Realm 和数据库的隔离副本用于升级验收。合成数据允许关闭 Worker。

| Fixture | 准备方法与用途 |
| --- | --- |
| Alice、Bob | 使用隔离 Realm 的已验证预置用户。保留既有主体和旧任务作为升级基准。浏览器分别通过正常 OIDC 登录取得各自的 `GET /api/v1/auth/me` 中的 `user_id`，用来装载报告。 |
| 新注册、待验证、未注册账号 | 每轮分配新的测试邮箱，例如 `tm-qa-<run>-new@example.test`、`tm-qa-<run>-pending@example.test`、`tm-qa-<run>-unknown@example.test`。待验证账号用独立注册流程创建，保留邮件等到对应步骤；未注册账号始终保留为空白对照。验证过期、验证重放、重置过期和重置重放各使用独立邮件。 |
| 浏览器会话 | Alice 的同源标签 A1、A2 共享 Cookie；Alice 的独立配置文件 B 使用另一 Keycloak 会话；Bob 的独立配置文件 C 用于隔离对照。用 A1、A2 观察标签页同步，用 A 与 B 观察设备隔离。 |
| 身份与通知 | 复用 `test_auth_phase2.py` 的临时签名密钥、JWKS 和 OIDC 测试替身。在隔离测试配置下给签名声明分配主体 `s1`、`s2`，Issuer `issuer-a.example`、`issuer-b.example`，共用显示邮箱 `identity@example.test`。同 Issuer 变更邮箱及姓名仍认作同一主体。另配 Alice 两个独立 `sid` 与 Bob 一个 `sid`，使用唯一 `jti` 和固定测试时钟覆盖回执、重放和并发。 |
| 敏感值检测 | 在隔离环境每轮生成唯一测试密码、SMTP 测试授权码；对当前流程的授权码、邮件操作令牌、OIDC Token、Cookie 和 CSRF 值只做内存比对。报告仅记录每类命中数与组件名。用安全的测试标记验证扫描确实读取了所选日志。 |
| 报告、证据和空闲流 | 报告脚本为已登录账号写入合成业务数据，不读取凭据。Alice 有两个同一发布时间的报告、同一任务的两次发布及一个较新草稿、单独的旧版未带发布事件报告和一个较早已发布报告。Bob 有一个发布时间晚于 Alice 所有报告的已发布报告。Alice 有一个不可被 Worker 领取的排队任务，两条流各有首帧，可保持空闲直至会话吊销。 |

从 `backend/` 运行以下命令。事先设置 `TEST_DATABASE_URL` 指向与候选应用共用的专用验收库，并把 `$aliceId`、`$bobId` 设为刚取得的 UUID。脚本通过一个事务写入任务、报告、发布事件、证据和研究来源，输出实际 UUID 及分页预期。每次装载生成一组新数据，同一轮只执行一次。重跑分页用例前恢复本轮数据库快照或使用新验收库，以免上轮数据进入列表。

```powershell
uv run python -m qa.seed_account_reports --alice-user-id $aliceId --bob-user-id $bobId
```

本地 Compose 的 PostgreSQL 只在容器网络内可达。可从候选版本仓库根目录把脚本经标准输入送入专用验收栈的 API 容器，连接配置在容器内读取。

```powershell
$qaCompose = @('--env-file', '.artifacts/local-oidc/.env', '-f', 'dev/local-oidc/compose.yaml')
Get-Content -Raw -Encoding UTF8 backend/qa/seed_account_reports.py |
    docker compose @qaCompose exec -T api sh -c `
        'TEST_DATABASE_URL=$DATABASE_URL python - --alice-user-id $1 --bob-user-id $2' `
        qa-fixture $aliceId $bobId
```

输出中 `alice_index` 按预期排序，`limit=2` 时依次得到 `latest revision, tie high` 和 `tie low, oldest`。高低以报告 UUID 排序，两个 tie 的 `published_at` 相同，顺序变化视为失败。Bob 只看见 `bob_index`。Alice 的 `alice_previous`、`alice_legacy` 应能读取详情；这两条和 `alice_draft` 都不得进入索引。使用 `alice_idle` 检查来源、证据及流。种子数据的标题均带 `QA` 前缀。

## 用例与验收标准

API 路径均相对 `/api/v1`。P0 属于阻断项，P1 是本次交付的必测回归。产品契约只规定错误类型的场合，以错误类型和可恢复性验收。

| 用例 | 优先级 | 测试与通过条件 |
| --- | --- | --- |
| AUTH-01 邮箱注册及重复 | P0 | 从入口进入 Keycloak，完成新邮箱注册并观察 SMTP 捕获到验证邮件。重复提交已验证和待验证邮箱，核对邮件投递与频率限制，确认没有第二个账号、主体或业务归属变化。比较浏览器可见的状态、跳转与提示，公开结果不可暴露账号是否存在。关闭账户功能时 `GET /auth/register` 为 `404 ACCOUNT_FEATURE_DISABLED`。 |
| AUTH-02 验证和访问门禁 | P0 | 新邮箱验证前，从同一浏览器访问 `/auth/me`、报告索引和创建任务，均为 `401 UNAUTHENTICATED`，数据库无有效业务会话。用签名 ID Token 分别确认 `email_verified` 缺失、`false`、字符串 `"true"`，以及 `email` 空、`sid` 缺失时均无有效会话；邮箱未验证回调为 `403 AUTH_EMAIL_UNVERIFIED`。按原生流程先验证再设密码。用独立链接检查过期与成功后重放；原 OIDC 事务已过期时，完成邮箱验证后可从登录入口重新授权。 |
| AUTH-03 正常登录、错误密码和回跳 | P0 | 既有用户按原方式、新用户按邮箱正确登录，`/auth/me` 返回当前账户，已登录浏览器可继续原私有页路径及其查询参数、锚点。错误密码、未知邮箱和已知邮箱加错误密码的公开失败提示不可区分是否注册，且均无业务会话。复用 state、浏览器 Cookie 绑定、回调重放、Issuer/Audience/Nonce/签名校验；核对非法或过期回调为 `400 AUTH_CALLBACK_INVALID`。授权、回调、编辑、退出和登录后回跳只允许契约的同源路径与固定返回地址。复用外部与协议相对路径拒绝断言。 |
| AUTH-04 忘记密码与重置 | P0 | 对已知、未知邮箱提交重置，比较公开状态、提示和跳转，捕获器仅对有资格的测试账号接收邮件。正常链接只使用一次，过期或已用链接不可再次改密。重置后要求重新登录，旧密码失败、新密码成功。使用第二个浏览器，在重置时勾选「退出其他设备」，确认真实后通道通知和相应旧会话、SSE 吊销。 |
| AUTH-05 资料与身份 | P0 | `/auth/me` 及资料页显示姓名、邮箱、验证状态，邮箱只读。带 CSRF 的 `POST /auth/profile/edit` 取得固定 `UPDATE_PROFILE` 授权地址，保存后回到 `/account`。取消保留原会话；发起会话已吊销、事务主体不同或事务无效时拒绝写入。通过签名声明变更姓名或邮箱时保留 `user_id` 与原任务；不同 `sub` 或不同 `issuer` 即使复用邮箱也生成不同用户且报告隔离。账户能力关闭时编辑为 `404 ACCOUNT_FEATURE_DISABLED`。 |
| AUTH-06 报告分页与访问隔离 | P0 | 两设备从 `/account/reports` 读取同一账号的服务端列表，不依赖缓存。断言版本、标题、发布事件时间、同时间 UUID 降序、末页空游标；`limit=1,2,50`、默认值及分页游标无重复或遗漏，非法游标或超范围 limit 为 `422 VALIDATION_ERROR`。Bob 使用 Alice 的游标也不能得到 Alice 的项目。旧版本及无发布事件的本人报告仍可点开；草稿、其他用户与公开示例不进入列表。用真实存在的跨用户 ID 对比不存在 ID，任务、报告、地图、路由、证据、研究来源和两条 SSE 跨用户统一为 `404 TASK_NOT_FOUND`。同一任务下缺失子资源仍用既有资源错误码。 |
| AUTH-07 OIDC 单点退出 | P0 | `POST /auth/logout/oidc` 持久化本地吊销并清 Cookie，先看到旧会话读请求及握手为 `401`，再顶层访问 Discovery 退出端点。核对固定 Client ID、精确注册的首页返回地址。经过可能出现的确认页后，立即再次登录要求重新认证。保留原 `POST /auth/logout` 的 `204` 契约。 |
| AUTH-08 Backchannel Logout | P0 | 在真实 Keycloak 管理端注销 Alice 单一设备，再注销全部设备，分别核对实际送达、对应会话、Bob 对照。集成测试复用签名 Fixture，验证唯一的表单字段、签名和算法、Issuer/Audience、`iat/exp/jti/sid`、注销事件和禁止 `nonce`，有 `sub` 时校验账户绑定。有效、重复、无匹配会话的有效通知均为 `200` 空响应；无效通知为 `400 {"error":"invalid_request"}`，未授权的会话不受影响。控制回调签发与注销的并发顺序，已吊销 SID 无法被在途回调恢复。关闭账户功能时入口为 `404 ACCOUNT_FEATURE_DISABLED`。 |
| AUTH-09 Cookie、CSRF 与固定过期 | P0 | 会话与流程 Cookie 检查 `__Host-`、`Secure`、`HttpOnly`、`SameSite=Lax`、`Path=/`、无 `Domain`；登录轮换后旧会话、旧 CSRF 不可复用。验证认证写请求缺失、重复、多值和跨源 Origin/CSRF 为 `403 CSRF_INVALID`，同源有效写入成功。使用隔离短 TTL 或固定数据时钟测试应用硬过期；持续访问不延长会话，API 与新 SSE 返回 `401`。检查启用账户模式后无 SID 的旧 Cookie 失效，原用户可重新登录取回报告。 |
| AUTH-10 吊销后的已连接 SSE | P0 | 让 `/events`、`/research-events` 都收到首帧并保持空闲。从吊销提交到服务端 EOF 分别计时，契约目标均为 6 秒以内。保持客户端打开，用独立探针确认服务端关闭流。新握手为 `401`，A1、A2 清理私有界面及重连定时器。仍授权的设备短暂断网再连，分别校验两流自己的 `Last-Event-ID` 或 `after`。 |
| AUTH-11 多设备与账号切换 | P0 | Alice A、B 同账号 ID、报告一致，各自有独立会话和 CSRF；A 的应用退出只使 A 的两标签失效，B 和 Bob 保持可用。验证同一 SID 的本地会话随通知吊销、其他 SID 保持可用；Keycloak 管理端全退出再观察各 SID。A 重新登录为 Bob 时清理 Alice 的内容缓存、在途请求和 SSE，后续页面仅显示 Bob 的数据。 |
| AUTH-12 依赖不可用与恢复 | P1 | 在隔离栈模拟 Keycloak Discovery、换码或 JWKS 暂不可用，确认需要 Provider 的账户请求为 `503 AUTH_PROVIDER_UNAVAILABLE`，提示可重试，无半成品会话。退出端点无法取得时本地仍退出且返回 `provider_logout_url: null`，界面提示 Provider 退出尚未确认。暂停隔离 SMTP 后，用已知和未知邮箱分别请求注册／重置，核验受控错误、无账号枚举差异和无验证绕过；恢复后验证重新发送／重试可完成。记下各故障的状态、耗时和恢复动作。 |

## 复用与自动化层

- 先跑现有 OIDC、所有权、任务 SSE 和 Research Browser 回归。账户候选版本有对应后端测试时沿用其服务器替身和数据库 Fixture，只补身份组合、发布分页边界、后通道签发竞争、两流吊销等缺口。现有 `test_open_sse_ends_after_session_is_revoked` 可参数化复用两条流，独立浏览器测试再量实际关闭时间。 [OIDC 与任务权限](../backend/tests/test_auth_phase2.py) · [Research Browser](../backend/tests/test_research_browser.py)
- 复用访问日志删除查询参数和数据库异常隐藏参数值的现有断言，真实栈验收时再检查各组件与实际邮件流程的日志。 [API 基础回归](../backend/tests/test_api.py)
- 复用前端登录回跳、401、账号分桶及重连取消测试。账户页、跨设备会话与真实 Provider 的联动留给浏览器验收。 [前端生命周期回归](../frontend/test/lifecycle.test.ts) · [两条流 API 回归](../frontend/test/desktop.test.ts)
- 运行固定用例以验证 Fixture 能在隔离 PostgreSQL 装载，并通过现有报告 API 读取实际报告、核对发布事件及证明跨用户拒绝。 [Fixture 回归](../backend/tests/test_account_auth_fixture.py)

```powershell
# backend/
uv run alembic upgrade head
uv run pytest -q tests/test_account_auth_fixture.py tests/test_auth_phase2.py tests/test_research_browser.py tests/test_api.py
# frontend/
npm test
```

自动化使用的 `DATABASE_URL` 和 `TEST_DATABASE_URL` 应指向同一个已隔离的回归库；浏览器 Fixture 装载时切换到浏览器验收库。

## A、B 合入后的执行顺序

1. 对同一候选提交检查 OpenAPI、HTTPS、Discovery、回调、SMTP 捕获和 Alembic。分别验证空 Realm 初次导入及已有 Realm 持久卷原地升级，核对 Alice/Bob 原主体和所有权，重复一次升级步骤确认幂等。额外运行账户关闭模式，确认旧通用 OIDC 登录兼容。
2. 跑后端和前端回归。用干净浏览器加正常登录建立 Alice、Bob，装载 Fixture，依次验证注册与验证、登录回跳、资料、密码恢复和报告。用新浏览器配置验证跨设备，保留尚未过期的会话给吊销测试。
3. 测应用退出、单点退出、真实 Keycloak 后通道、会话固定过期和两条已连接 SSE。最后测依赖故障并恢复服务，重新验证一次正常登录及邮件路径。
4. 检查 Caddy、FastAPI、Keycloak 与邮件运维日志。回调访问仅记录路径和结果码，检查各日志均未出现密码、SMTP 授权码、授权码、邮件链接、Token、Cookie 或 CSRF，泄露命中计数须为零。认证、资料、报告索引响应须带 `Cache-Control: no-store`，授权与回调须带 `Referrer-Policy: no-referrer`。证据保留脱敏后的状态、计数、是否跳转和耗时。

按 `用例 ID | 候选提交与环境 | 浏览器/API/数据库结果 | 证据编号 | 状态或缺陷` 记录结果，状态使用「通过／失败／阻塞／未执行」。两条流分别记录实际耗时。身份隔离、会话门禁、开放重定向、敏感日志或账号枚举失败需要在公开发布前处理。

## 最终集成实测 · 2026-11-15

隔离 Compose 使用 Keycloak 26.8.2、Mailpit、真实 FastAPI、PostgreSQL 17、Next.js 生产构建和受信的本地 HTTPS。通过真实 Chrome 执行身份操作。报告和空闲调研任务使用上述 PostgreSQL Fixture。

| 用例 | 结果 |
| --- | --- |
| AUTH-01 至 AUTH-03 注册、验证、登录 | 真实注册收到 SMTP 验证邮件。验证前 `/auth/me` 和报告列表返回 401。打开邮件完成验证和密码设置后建立应用会话，已验证邮箱显示在个人资料。Alice、Bob 分别通过 Keycloak 登录。使用已注册邮箱重复提交时没有创建第二个账户，但 Keycloak 表单显示邮箱字段错误。 |
| AUTH-04 忘记密码 | 实际收到重置邮件。旧密码被拒绝，新密码可登录原账户，重复打开已用链接没有改密表单。选择退出其他设备后，同一个账户的两个旧应用会话均返回 401。已知和未知邮箱的提交提示一致，只有已知邮箱收到邮件。 |
| AUTH-05 资料与授权 | 实际修改昵称和取消编辑均回到个人资料页，邮箱只读，用户 ID 保持一致。取消保留原会话。缺少 CSRF 的编辑请求返回 403。 |
| AUTH-06 报告与双用户隔离 | 两个设备读到相同报告索引，四条发布报告用两页读完。增加合成数据后，实际报告页验证了 20＋1 条和上一页导航。Alice 可打开本人历史报告。Bob 对 8 个真实存在的他人私有路径收到 404，涉及任务、报告、地图、路线、证据、来源和两条流。 |
| AUTH-07 至 AUTH-11 退出及会话 | 应用 OIDC 退出后再次登录显示 Keycloak 凭据表单。此设备的两条既有流均约 4.95 秒 EOF，同账号另一设备仍登录。真实 Keycloak 管理端发出后通道通知后，两条流均约 5.00 秒 EOF，旧会话与新握手均返回 401，Bob 继续可用。检查实际 Cookie 的主机范围、Secure、HttpOnly 和 SameSite。 |
| 自动化、安全和数据库 | PostgreSQL 上后端全量 290 项，前端 35 项，Ruff、ESLint、TypeScript、OpenAPI 类型生成、Next.js 生产构建及 Compose 配置检查通过。Alembic `0006 → 0007 → 0006 → 0007` 保留旧用户、会话和任务归属。Python 76 个包及 npm 437 个包名和版本组合无匹配安全公告。真实栈日志内存检查覆盖 19 个测试敏感值，命中 0。 |
| Realm 导入及升级 | 隔离验收栈从空卷导入账户 Realm，真实浏览器随后完成注册。独立旧版 Realm 连续两次运行 `upgrade-auth`，保留 Alice、Bob 的主体 ID 和原回调，同时启用主题、邮箱验证及 Mailpit SMTP。 |

公开注册仍有一项阻塞。Keycloak 原生注册对重复邮箱显示字段错误，能够通过提交后的表单状态识别已注册邮箱。现有账户和报告归属保持独立。受控本地环境可验收原生流程，公网自助注册需先明确并处理这一暴露面。邮件由本地 SMTP 捕获器实际接收。生产邮箱送达、邮件过期时钟和依赖故障演练需在目标部署中继续验收。
