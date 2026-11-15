# TravelMind 登录、注册与账户管理契约

本契约以本地 `main` 的 `123c237` 为审查基线。本文中的新增接口、迁移及 Realm 配置均为后续实现要求。

## 当前实现与缺口

| 范围 | `123c237` 的实际行为 |
| --- | --- |
| 本地 Realm 和 Client | Keycloak `26.8.2`，Realm `travelmind-dev`，机密客户端 `travelmind-local`。授权码、PKCE S256、RS256、`client_secret_basic`。隐式授权、密码授权和服务账户关闭。Issuer 为 `https://id.travelmind.localhost:18443/realms/travelmind-dev`，回调为 `https://travelmind.localhost:18443/api/v1/auth/callback`。Caddy 只发布回环 HTTPS，代理开发 Realm 和身份页面静态资源。 [Realm](../dev/local-oidc/realm.json) · [Compose](../dev/local-oidc/compose.yaml) · [Caddy](../dev/local-oidc/Caddyfile) |
| 登录 | 首页的「登录 / 注册」链接请求 `GET /api/v1/auth/login`。FastAPI 用 `openid profile` 发起授权，在 PostgreSQL 创建绑定浏览器 Cookie 的一次性流程，10 分钟过期。回调核对 `state`，携 PKCE verifier 换码，经 Discovery 和 JWKS 校验签名、Issuer、Audience、时间和 `nonce`。成功后轮换应用会话，跳到 `/workbench`，再由前端读取一次性同源回跳地址。一个浏览器只有一个登录流程 Cookie，第二次发起会覆盖它。 [认证路由](../backend/travelmind/api/auth.py) · [OIDC 校验](../backend/travelmind/api/oidc.py) · [回跳](../frontend/src/lib/login-return.ts) |
| 注册、验证、恢复 | Realm 将 `registrationAllowed` 和 `resetPasswordAllowed` 设为 `false`，未配置 `verifyEmail` 或 SMTP。Alice、Bob 为预置且已标记邮箱验证的账号。真实邮件验证和重置尚未接通。 [Realm](../dev/local-oidc/realm.json) |
| 账户前端 | `SessionProvider` 管理加载中、匿名、已登录和不可用四种状态。启动、焦点、页面恢复和已登录可见页每 30 秒检查会话。菜单展示姓名首字、我的报告和退出。「我的报告」指向 `/reports`，列表读取按用户分桶的本机 `localStorage`，最多 20 条，跨设备只能凭已知报告链接访问。缺少个人资料页。 [SessionProvider](../frontend/src/components/session.tsx) · [账户菜单](../frontend/src/components/site-header.tsx) · [报告缓存](../frontend/src/lib/report-history.ts) |
| 身份和归属 | `users` 用 `(issuer, subject)` 唯一映射 `user_id`，仅同步 ID Token 的 `name`。`/auth/me` 返回 `user_id`、`display_name`、`csrf_token`。任务使用 `owner_id`，报告、证据、地图、来源及两条 SSE 先校验任务所有权。不存在和无权访问统一返回 404。 [用户和会话持久化](../backend/travelmind/db/auth.py) · [任务接口](../backend/travelmind/api/tasks.py) |
| Cookie、CSRF 和吊销 | `__Host-travelmind-session` 和登录流程 Cookie 均为 `Secure`、`HttpOnly`、`SameSite=Lax`、`Path=/`，无 `Domain`。数据库保存会话令牌哈希，固定有效期默认 8 小时，由 `OIDC_SESSION_SECONDS` 配置。认证写请求需唯一的同源 `Origin` 和 `X-CSRF-Token`。`POST /auth/logout` 吊销当前应用会话并清除 Cookie，返回 204。Keycloak SSO 可继续有效，FastAPI 缺少 Keycloak Session ID 和后通道吊销入口。两条 SSE 在握手时检查会话和所有权，连接内每 5 秒复查会话。 [认证依赖](../backend/travelmind/api/dependencies.py) · [会话持久化](../backend/travelmind/db/auth.py) · [SSE](../backend/travelmind/api/tasks.py) |

## 认证与资料归属

- Keycloak 管理邮箱注册、验证邮件、密码策略、密码重置和姓名编辑。Next.js 提供入口和状态反馈。FastAPI 接收 OIDC 授权回调、管理应用会话和本地用户映射。凭据和邮箱操作令牌由 Keycloak 处理。浏览器继续使用同源 API 与 SSE，以及现有应用 Cookie。
- 账户功能由 `OIDC_ACCOUNT_ENABLED` 显式启用，默认 `false` 以兼容已有外部 OIDC 配置。启用后申请 `openid profile email`，并要求已验证 ID Token 包含有效 `sid`、非空 `email` 和 JSON 布尔值 `email_verified: true`，才能创建或轮换会话。旧会话中缺少 `sid` 的记录在启用时失效，用户重新登录后可继续访问原有任务。
- `users` 增加可空 `email` 和 `email_verified`，由通过校验的 OIDC 回调同步。姓名和邮箱是显示资料，唯一账户键仍为 `(issuer, subject)`。同一主体更名或修改邮箱保留 `user_id`。不同主体使用同一邮箱时保持独立，Realm 或 Issuer 迁移需另行处理映射。
- 资料页展示姓名、邮箱及验证状态。首版允许通过 Keycloak 更新姓名，邮箱在 TravelMind 只读。更新姓名复用 Keycloak `UPDATE_PROFILE` Application Initiated Action（AIA）。同一授权事务绑定发起会话和预期主体。回调须校验发起会话仍有效和主体一致。校验过的取消流程回到资料页，保留原会话。用换码后的签名 claims 同步资料，浏览器回调参数只用于展示流程结果。 [Keycloak AIA](https://www.keycloak.org/docs/26.8.2/server_admin/)

## 公共接口

所有路径以同源 `/api/v1` 为前缀。浏览器认证写接口使用现有 `Origin` 和 `X-CSRF-Token`。认证、资料和报告索引响应使用 `Cache-Control: no-store`，授权及回调响应使用 `Referrer-Policy: no-referrer`。

| 方法及路径 | 成功与权限 |
| --- | --- |
| `GET /auth/login` | 保留 `302` 和现有授权流程。Keycloak 登录页提供「忘记密码」，进入原生 Reset Credentials 流程。 |
| `GET /auth/register` | 启用后返回 `302`，复用登录流程并附 `prompt=create`。关闭时返回 `404 ACCOUNT_FEATURE_DISABLED`。 |
| `GET /auth/callback` | 登录、注册验证完成及资料编辑复用此回调。成功 `302`。现有登录默认 `/workbench`，编辑回 `/account`。非法或过期事务 `400 AUTH_CALLBACK_INVALID`，账户模式下邮箱未通过验证为 `403 AUTH_EMAIL_UNVERIFIED`。前端回跳仅支持应用内路径，服务端编辑目标固定。 |
| `GET /auth/me` | 已登录返回 `200 CurrentUser`。保留 `user_id: UUID`、`display_name: string \| null`、`csrf_token: string`，增量提供 `email?: string \| null`、`email_verified?: boolean \| null`、`account_management_enabled?: boolean`。旧或通用 OIDC 模式的未知资料可空，缺少能力字段按 `false` 处理。 |
| `POST /auth/profile/edit` | 无请求体，要求已登录、账户功能开启和 CSRF。返回 `200 { authorization_url: string }` 并设置登录流程 Cookie。URL 由服务端用固定 `kc_action=UPDATE_PROFILE` 生成。浏览器顶层导航至该 URL。关闭时返回 `404 ACCOUNT_FEATURE_DISABLED`。 |
| `GET /account/reports?limit=20&cursor=…` | 已登录返回 `200 { items: ReportSummary[], next_cursor: string \| null }`。`ReportSummary` 包含 `task_id: UUID`、`report_id: UUID`、`version: int`、`title: string`、`published_at: ISO 8601 UTC`。`limit` 取 1 至 50，cursor 是服务端生成的不透明游标，非法参数返回 `422 VALIDATION_ERROR`。 |
| `POST /auth/logout` | 保留已登录且通过 CSRF 后清 Cookie 并返回 `204` 的原契约。 |
| `POST /auth/logout/oidc` | 无请求体，要求已登录、账户功能开启和 CSRF。先持久化应用会话吊销并清 Cookie，返回 `200 { provider_logout_url: string \| null }`。有 URL 时顶层导航至 Discovery 的 `end_session_endpoint`，附固定 Client ID 和注册的 `post_logout_redirect_uri`。Keycloak 可显示退出确认，确认后返回应用首页。端点暂不可用时返回 `null` 并提示应用已退出，Keycloak 退出尚未确认。关闭时返回 `404 ACCOUNT_FEATURE_DISABLED`。 |
| `POST /auth/backchannel-logout` | Keycloak 以 `application/x-www-form-urlencoded` 提交唯一 `logout_token`。服务间请求靠签名令牌认证。有效通知、重复通知和找不到对应会话均返回 `200` 空响应，非法令牌返回 `400 { error: "invalid_request" }`。关闭时返回 `404 ACCOUNT_FEATURE_DISABLED`。 |

面向应用的错误沿用 `ErrorResponse` 的 `detail.code`、`detail.message`、`detail.retryable`。匿名、会话过期或已吊销为 `401 UNAUTHENTICATED`，不合法的写请求来源或 CSRF 为 `403 CSRF_INVALID`，账户模式下外部 OIDC 服务临时失败采用 `503 AUTH_PROVIDER_UNAVAILABLE`。保留现有资源 404 和报告、地图、证据读取路径。 [Phase 2 错误与身份约定](phase-2-contract.md#错误与幂等)

### 我的报告

在服务端用会话的 `user_id` 过滤 `research_tasks.owner_id`。以已提交的 `report.ready` 事件为发布凭证，按任务取最后一次发布，按任务 ID 和报告 ID 关联 `reports` 行，`published_at` 来自发布事件时间。按 `(published_at, report_id)` 降序游标分页，游标独立于所有权判断。报告列表只含已发布报告，报告详情仍可通过已知链接读取本人拥有的历史版本。没有 `report.ready` 记录的老报告保留原有详情访问。示例报告继续通过独立的公开入口展示。

### 会话与单点退出

- 保持本地固定过期策略。Keycloak Realm 的 SSO 和 Client Session 使用独立时钟，分别配置并验证最大有效期。OIDC 通知用于传播显式吊销，应用 Cookie 的硬过期仍由 PostgreSQL 判定。
- `sessions` 增加索引支持的 `oidc_sid`，从签名 ID Token 取得。登录流程记录预期主体与发起会话所需元数据。后通道接收端校验签名、允许的算法、Issuer、Audience、`iat`、`exp`、`jti`、`sid`、注销 `events` 对象及 `nonce` 缺失。Keycloak 26.8.2 在 Client 要求 Session ID 时签发含 `sid` 的两分钟有效令牌。按同 Issuer／Client 的 `sid` 吊销关联会话，若带 `sub` 还须与账户匹配。以限期回执保留 `(issuer, client_id, jti)` 的幂等记录，并对该 `sid` 同步吊销与签发，防止在途登录回调恢复已吊销会话。管理端多设备吊销通过 Keycloak 各会话通知传播。 [OIDC Back-Channel Logout](https://openid.net/specs/openid-connect-backchannel-1_0.html) · [Keycloak 26.8.2 Logout Token](https://github.com/keycloak/keycloak/blob/26.8.2/services/src/main/java/org/keycloak/jose/jws/DefaultTokenManager.java#L327)
- RP 退出使用 `client_id` 和精确注册的返回地址，接受 Keycloak 原生确认页。应用无需持久化 ID、access 或 refresh token。代理和应用日志只记录认证路径和结果码，避免记录回调查询串、邮件操作链接、Cookie 值、Token 和 CSRF。 [OIDC RP-Initiated Logout](https://openid.net/specs/openid-connect-rpinitiated-1_0.html)
- 应用会话吊销提交后，新 API 和 SSE 握手返回 401。两条已打开 SSE 在下一次会话复查关闭，验收以空闲流 6 秒以内为目标，覆盖 5 秒检查间隔和 1 秒空闲轮询。同一会话关联的浏览器标签应清理私有加载状态和重连定时器。授权仍有效时，重连继续使用各流自己的 `Last-Event-ID` 或 `after`。

## Keycloak Realm 与部署

| 范围 | 实现配置 |
| --- | --- |
| 登录和注册 | 在启用账户功能的 Realm 设置 `registrationAllowed=true`、`loginWithEmailAllowed=true`、`duplicateEmailsAllowed=false`、`verifyEmail=true`、`resetPasswordAllowed=true`。保留用户名字段及 Alice/Bob 原有登录方式，新的用户可以用邮箱登录。使用 26.8.2 原生的先验证邮箱再设密码流程。启用 `UPDATE_PASSWORD`、`UPDATE_PROFILE`、`VERIFY_EMAIL`，配置密码策略、登录防暴力和注册、邮件发送频率限制。 [Keycloak 注册及登录配置](https://www.keycloak.org/docs/26.8.2/server_admin/) |
| 邮件和时效 | 本地使用隔离 SMTP 捕获器，查看邮件的端口只绑定回环。生产由 Realm 配置 SMTP Host、Port、From、TLS 和认证凭据。建议默认验证链接 30 分钟、重置链接 15 分钟。链接使用对浏览器可达的 Keycloak HTTPS 域名。用户打开邮件时若原授权流程已过期，应允许完成验证后返回登录入口重新开始授权。 [Keycloak 邮件和令牌时效](https://www.keycloak.org/docs/26.8.2/server_admin/) |
| 重置 | Reset Credentials 的 `Send Reset Email` 设置 `Force login after reset=true`。在两设备验收中使用 Keycloak 原生「退出其他设备」选项验证后通道吊销。全设备自动吊销取决于 Realm 的重置行为和通知送达。检查未知账号与已知账号走相同的公开响应，邮件链接过期、完成后重放均无法再次修改凭据。 [Keycloak Reset Credentials](https://www.keycloak.org/docs/26.8.2/server_admin/) · [26.8.2 UpdatePassword 实现](https://github.com/keycloak/keycloak/blob/26.8.2/services/src/main/java/org/keycloak/authentication/requiredactions/UpdatePassword.java) |
| Client | 保持机密客户端、授权码、PKCE S256、RS256、精确注册登录回调。验证 `profile` 和 `email` Scope 的 `name`、`email`、`email_verified` 以及原生 `sid` 实际进入签名 ID Token。开发环境精确注册退出返回 `https://travelmind.localhost:18443/`，Backchannel Logout URL 为 `https://travelmind.localhost:18443/api/v1/auth/backchannel-logout`，关闭 Front Channel Logout，开启 Backchannel Logout Session Required。生产使用相应精确 HTTPS 地址。 [Keycloak OIDC Client 设置](https://www.keycloak.org/docs/26.8.2/server_admin/) |
| 网络和持久 Realm | 检查浏览器、FastAPI 的 Discovery/Token/JWKS 请求和 Keycloak 的回调请求均能验证 HTTPS 并按配置到达。为本地 Keycloak 提供应用域的容器 DNS 和 mkcert CA 信任。管理面仍保持私有。本地初次启动导入与现有 Realm 原地更新分别验收，`--import-realm` 会跳过已存在的 Realm，升级脚本需幂等应用 Realm/Client 改动并保留用户及其主体 ID。开发邮箱、凭据和证书留在本地。 [Keycloak Realm 导入](https://www.keycloak.org/server/importExport) |

## 后续实现边界

| 负责面 | 文件和依赖 |
| --- | --- |
| Keycloak / 运维 | `dev/local-oidc/realm.json`、`compose.yaml`、`Caddyfile`、`stack.ps1` 及启动说明。先交付隔离 SMTP、回调证书信任、持久 Realm 原地更新和真实 Keycloak 配置样例。 |
| FastAPI / 存储 | `api/auth.py`、`api/oidc.py`、`api/dependencies.py`、账户报告索引路由与查询、`db/auth.py`、`db/models.py`、`schemas/api.py` 及新增 Alembic 迁移。迁移负责资料字段、流程绑定、会话 SID 及有限期回执。由后端提交 OpenAPI，再生成前端类型。保留现有所有权判断与 AgentEvent v1。 |
| Next.js | `components/session.tsx`、`components/site-header.tsx`、登录回跳、`/account` 与报告列表。消费生成的 OpenAPI 类型，按会话区分加载、空结果、匿名和故障。登录入口始终可用，托管登录页根据 Realm 显示注册和忘记密码。已登录菜单按会话声明的能力选择编辑和退出方式。账户切换清理前账号的私有状态、在途请求和 SSE，服务端列表提供跨设备历史。保留报告示例和旧链接。 |

## 后续验收

- 本地受信 HTTPS 真浏览器和真实 Keycloak 登录。既有 Alice/Bob 和新注册邮箱各自进入账户，非预期 `state`、重放、错误 Provider 或回调主体不匹配均被拒绝，直达私有页的登录回跳仍可用。
- 通过本地 SMTP 捕获器接收注册验证邮件。未验证账号既无法建立会话也无法访问 API。覆盖验证链接过期或使用后重放、超过原授权期限后重新登录，以及重置请求公开提示一致、重置链接过期或重放、旧密码失败、新密码登录。
- 资料页展示真实邮箱状态，姓名编辑和取消流程均可正确返回。相同 `(issuer, sub)` 保留既有 `user_id` 与报告。另一个 `sub` 或 Issuer 即使邮箱相同也隔离。跨设备可读取报告列表，排序及分页稳定，草稿、其他用户和样例不出现在该账户列表中。跨用户任务、报告及地图、研究资源和两条 SSE 握手均为 404。
- 检验 Cookie 属性、CSRF 缺失／重复／跨源拒绝、绝对过期和会话轮换。应用退出后旧 Cookie 为 401，两条 SSE 关闭。完整 Keycloak 退出后立即再登录须重新认证。管理员注销和重置时选择退出其他设备均实测送达后通道，验证重复通知、被拒绝的通知、会话签发竞争及多设备隔离。
- 专用 PostgreSQL 验证迁移和原有所有权。验证首次导入、已有 Keycloak 数据卷升级、无邮件服务时的提示、日志脱敏及关闭账户模式后的通用 OIDC 登录。对真实链路和自动化回归分别记录结果。

## 本次审查验证

运行中的本地 Keycloak 容器为固定版 `26.8.2`。`stack.ps1 check` 验证受信 HTTPS、Discovery、JWKS 与 PKCE S256 授权跳转。实际响应公开 `end_session_endpoint` 和后通道支持元数据。获取跳转后的 Keycloak HTML，看到登录表单，注册和忘记密码链接均未渲染。匿名 `/auth/me` 返回 401。

隔离 PostgreSQL 17 应用既有 Alembic 迁移，`test_auth_phase2.py` 和 `test_research_browser.py` 共 5 项通过，覆盖现有 OIDC 交换和吊销、CSRF、资源归属、SSE 重放和断开；其中 OIDC 供应商响应为测试替身。前端 `npm test` 31 项通过。本轮未执行浏览器凭据登录、发送验证或重置邮件、Keycloak 全局退出和后通道端到端验收。
