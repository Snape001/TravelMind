# TravelMind 账户认证部署

## 本地开发

使用 Windows 本地 OIDC 开发栈。需要 Docker Desktop、Compose 2.24+、mkcert，以及已配置的后端环境。Keycloak 的数据卷保存身份资料和用户凭据，PostgreSQL 保存应用用户映射、显示资料和会话哈希。

首次启动运行：

```powershell
.\dev\local-oidc\stack.ps1 start
.\dev\local-oidc\stack.ps1 check
```

日常 `start` 优先复用已有应用镜像，缺少镜像时自动构建。部署更新过的源码或依赖时使用 `start -Build`。

已有本地部署先升级原 Realm，再重建应用。升级短暂停止本机 Keycloak，通过一次性私有管理员原地更新 Realm 和客户端，保留 Alice、Bob、已注册用户、主体 ID 和数据卷。已有仅含旧会话信息的 Cookie 在账户模式下需重新登录。

```powershell
.\dev\local-oidc\stack.ps1 upgrade-auth
.\dev\local-oidc\stack.ps1 start -Build
.\dev\local-oidc\stack.ps1 check
```

从 `https://travelmind.localhost:18443` 进入登录页，或直接打开同源 `/api/v1/auth/register`。填写用户名、邮箱和姓名，随后在 `http://127.0.0.1:18025` 的 Mailpit 打开验证链接，再设置密码。开发 Realm 使用 Compose 内的 `mailpit:1025` 和发件地址 `no-reply@travelmind.test`，无需 SMTP 密钥。收件箱只绑定本机回环，邮件留在本地。验证链接有效期 30 分钟，密码重置链接 15 分钟。如果打开邮件时 TravelMind 的 10 分钟授权事务已过期，完成 Keycloak 验证或设定密码后返回应用登录入口重新授权。

开发 Compose 挂载 TravelMind 托管登录主题，空 Realm 导入及 `upgrade-auth` 均选用该主题。运行 `stack.ps1 stop` 停止容器并保留 Realm 和业务数据卷。 [主题安装](../frontend/keycloak-theme/README.md)

密码要求 12 至 128 位，且不能等于用户名或邮箱。Keycloak 的默认姓名字段用于应用昵称，邮箱唯一且可用作登录名，原测试账号仍支持用户名。启用了登录失败锁定、原生验证邮件重发冷却和重置后重新认证。重置时选择 **Sign out from other devices** 可以让 Keycloak 吊销其它登录会话。

### 使用 QQ 邮箱发送验证邮件

登录 QQ 邮箱网页版，在「设置 → 账号与安全 → 安全设置」中开启 `POP3/SMTP` 或 `IMAP/SMTP` 服务并生成授权码。 [QQ 邮箱](https://mail.qq.com/)

在项目根目录运行以下命令，可在本地栈运行时直接执行。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\dev\local-oidc\stack.ps1 set-qq-smtp
```

根据提示输入发件用的完整 QQ 邮箱地址和 SMTP 授权码。授权码以隐藏输入读取，随 SMTP 设置保存在本地 Keycloak 数据卷中。命令短暂重启 Keycloak 并原地更新 `travelmind-dev`，使用 `smtp.qq.com:465` 和隐式 TLS。已有用户、任务和授权配置保留，后续 `start` 和 `upgrade-auth` 保留 SMTP 设置。 [腾讯云 SMTP 参数](https://cloud.tencent.com/document/product/1207/45117)

切换后从 TravelMind 的验证页点「重新发送」，在注册邮箱查看新邮件。验证链接仍指向本地 HTTPS 域名，请在运行 TravelMind 的电脑上打开。命令成功代表设置已保存，实际送达以收件箱为准。

## 生产环境

1. 启动生产模式的 Keycloak 26.8.2，使用持久数据库、独立身份域名和受信 HTTPS。复制 [环境模板](../dev/local-oidc/.env.example) 到 Git 忽略的环境文件。向 Keycloak 注入 `TM_SMTP_HOST`、`TM_SMTP_PORT`、`TM_SMTP_FROM`、`TM_SMTP_USER`、`TM_SMTP_PASSWORD`、`TM_SMTP_SSL` 和 `TM_SMTP_STARTTLS`。将 [生产 Realm 模板](../dev/local-oidc/realm.production.json) 挂载到 `/opt/keycloak/data/import`，空 Realm 使用 `start --import-realm`。生产模板无开发测试用户，主题和实际用户协议由部署方选用及配置。 [Keycloak 导入](https://www.keycloak.org/server/importExport)
2. 已有 Realm 通过私有管理面原地修改，保留原 Realm 名、客户端、签名密钥和用户。用模板核对 Login、Email、Tokens、Sessions、密码策略和 Client 配置。启用 `UPDATE_EMAIL` Required Action，让 Keycloak 单独处理邮箱变更与验证，TravelMind 的 `UPDATE_PROFILE` 入口用于修改姓名。复制原生 Reset Credentials 流程，将 **Send Reset Email → Force login after reset** 设为 `true`，再绑定到 Realm。Host、用户和授权码从部署环境传入。SMTP 隐式 TLS 使用 465，STARTTLS 通常使用 587，按供应商要求选择一种。 [Keycloak Realm 邮件设置](https://www.keycloak.org/docs/26.8.2/server_admin/#_email)
3. 先执行 `alembic upgrade head`。FastAPI 进程设 `OIDC_ACCOUNT_ENABLED=true`、`OIDC_ISSUER=https://id.travelmind.example/realms/travelmind`、`OIDC_PUBLIC_ORIGIN=https://travelmind.example`、`OIDC_CLIENT_ID`、`OIDC_CLIENT_SECRET` 和 `OIDC_TOKEN_AUTH_METHOD=client_secret_basic`。独立运行也可把这些变量填入 `backend/.env.oidc`。应用固定会话时钟默认 8 小时，Keycloak SSO 与 Client Session 各自也有独立的 8 小时最大寿命和 30 分钟闲置计时。
4. 在 Client 精确登记登录回调 `https://travelmind.example/api/v1/auth/callback`、退出返回 `https://travelmind.example/` 和后通道地址 `https://travelmind.example/api/v1/auth/backchannel-logout`。保持 PKCE S256、RS256、Backchannel Logout Session Required。应用端需要访问 Keycloak Discovery、JWKS 和 Token URL，Keycloak 服务端也需能解析并信任应用 HTTPS 域名。后通道按 OIDC 签名认证，反代需放行该 POST 并保留表单正文。两条 SSE 路径保持禁用响应缓冲及长连接。 [OIDC 后通道退出](https://openid.net/specs/openid-connect-backchannel-1_0.html)
5. 登录私有 Keycloak Admin Console，确认管理员账号配置了接收测试邮件的邮箱，然后使用 **Realm settings → Email → Test connection**。SMTP 测试成功以 Keycloak 接受请求和实际收件记录为准。随后以两个测试用户分别完成注册、验证、重置和退出。退出时若应用返回空的 `provider_logout_url`，应用会话已吊销，Keycloak 退出仍需重试。 [OIDC RP-Initiated Logout](https://openid.net/specs/openid-connect-rpinitiated-1_0.html)

公网开放自助注册前需处理原生注册的邮箱枚举差异。现有 Keycloak 26.8.2 对已使用邮箱显示字段错误。限制注册尝试频率可降低滥用，表单差异仍然存在。 [浏览器实测](account-auth-qa.md#最终集成实测--2026-11-15)

生产反代应对注册、重置和验证重发的 POST 请求限流。例如在 Keycloak 的 Nginx 入口使用以下配置，阈值可按共享出口网络和发送配额调整。管理路由留在私网。访问日志使用 `$uri` 和状态码，省略查询串、Cookie、认证头及正文。

```nginx
# http {}
map $request_method $account_write_key {
    default "";
    POST $binary_remote_addr;
}
limit_req_zone $account_write_key zone=keycloak_account_writes:10m rate=10r/m;

# Keycloak server {}
location ~ ^/realms/[^/]+/login-actions/(registration|reset-credentials|required-action)$ {
    limit_req zone=keycloak_account_writes burst=10 nodelay;
    limit_req_status 429;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-Port 443;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_pass http://keycloak:8080;
}
```

## 应用接口及回归

`GET /api/v1/auth/me` 返回应用用户 UUID、显示姓名、邮箱与验证状态、可空的 HTTPS `avatar_url` 和 CSRF Token。头像来自签名 ID Token 的 `picture`，默认本地 Keycloak 未配置头像时返回 `null`。编辑使用既有 CSRF 保护，并通过 Keycloak 更新姓名。报告列表通过 `GET /api/v1/account/reports` 按游标分页，从本人任务的最后一次 `report.ready` 读取，可继续用旧链接访问本人历史报告。

在专用 PostgreSQL 数据库执行 `alembic upgrade head`，设置 `TEST_DATABASE_URL` 后，在 `backend` 运行 `uv run pytest tests/test_auth_phase2.py tests/test_account.py tests/test_research_browser.py`。浏览器验收可用两个无痕会话验证跨账户报告、证据和两条 SSE 为 404。Keycloak 管理端注销同一个用户后，旧应用 Cookie 返回 401，两条已打开的流在下一次会话检查关闭。SMTP 测试不依赖 TravelMind Worker 或研究供应商。
