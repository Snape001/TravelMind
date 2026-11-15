# TravelMind 托管认证主题

把此目录中的 `travelmind` 安装到 Keycloak 26.8.2 的 `/opt/keycloak/themes/travelmind`。在目标 Realm 的 **Realm settings → Themes → Login theme** 选用 `travelmind`。简体中文 Realm 在 **Localization** 中启用 `zh-Hans`。主题继承原生表单及流程，通过样式和消息包应用 TravelMind 视觉。

- 登录页使用原生邮箱或用户名及密码字段。启用自助注册和找回密码后，入口会出现在登录页。TravelMind 的注册入口使用同源 `GET /api/v1/auth/register` 发起授权。
- 注册使用契约指定的流程。先填写身份资料并同意协议，再在邮箱验证后设置密码和确认密码。验证邮件提示及重发使用 Keycloak 原生步骤。
- 在 Realm 用户资料中将用于 ID Token `name` 的字段标记为「昵称」。Realm 管理方启用原生 **Terms and Conditions** 注册表单步骤，并在 Realm 本地化文本中配置正式的 `termsText`。邮箱验证、SMTP 和重置由 Realm 管理。
- 资料编辑通过后端发起 `UPDATE_PROFILE`。返回应用后，资料页重新读取会话中的昵称及邮箱状态。关闭账户功能的 OIDC 会话仍然可以本地退出。

账户接口、配置与浏览器联调清单位于 [冻结契约](../../docs/account-auth-contract.md)。安装或选用主题时，可参照 [Keycloak 主题指南](https://www.keycloak.org/ui-customization/themes)。
