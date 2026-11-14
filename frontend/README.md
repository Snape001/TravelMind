# Phase 2 前端任务生命周期

## 使用契约 Fixture

在本目录运行以下 PowerShell 命令。

```powershell
npm ci
$env:NEXT_PUBLIC_RESEARCH_MODE = "mock"
npm run dev -- --hostname 127.0.0.1
```

从首页登录测试账号并创建任务。工作台支持暂停和恢复。Fixture 会发布一轮澄清，回答两题后继续生成报告。重新打开任务可回放事件，从连接中断处恢复时按最后接受的序号请求后续事件。退出会撤销 Fixture 会话。Fixture 账号、任务和报告数据随 Next.js 进程重启清空。

## 正式 API

默认通过同源 `/api/v1` 访问任务服务。OIDC 登录和回调走浏览器导航，会话由任务服务的 Cookie 管理。前端从 `GET /auth/me` 读取登录状态，并在内存中保留 CSRF 令牌用于写请求。

冻结契约提供任务详情。调研历史显示本浏览器对当前账号保存的最近 20 个任务链接，每次展示时重新读取任务详情并依赖服务端校验归属。新设备可用已知任务链接直达详情。

## 验证与合并

```powershell
npm run test
npm run lint
npm run typecheck
npm run build
```

纯前端测试覆盖 AgentEvent v1 回放和续传、暂停确认、恢复、澄清答复、会话与 CSRF、幂等响应和本地历史隔离。

合并验收使用最终任务服务的 OpenAPI。任务服务在默认本地端口 8000 启动后，运行 `npm run generate:api`，核对并提交 `src/lib/api.generated.ts` 的变化，然后重新运行上述检查。也可用 `npx openapi-typescript <OpenAPI 文件或 URL> -o src/lib/api.generated.ts` 对已发布的 schema 生成同一文件。
