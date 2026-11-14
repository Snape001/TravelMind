# Phase 2 独立调研 Worker

Worker 通过冻结的 `WorkerStore` 领取任务、续租、读取指令和提交业务投影。合入任务服务分支后，入口使用其 PostgreSQL 实现 `travelmind.db.worker.PostgresWorkerStore`。

## 本地启动

合入任务服务和 Runtime 后，从 `backend` 目录执行以下命令。使用与 FastAPI 相同的数据库配置，并设置 `DEEPSEEK_API_KEY` 及需要的检索服务密钥。

```powershell
uv sync --locked
uv run alembic upgrade head
uv run python -m travelmind.runtime.worker_cli init-checkpoints
uv run python -m travelmind.runtime.worker_cli run
```

部署时先停用旧版 FastAPI 后台执行器，再运行迁移及一次 Checkpoint 初始化。Worker 进程可独立重启或启动多个实例。默认租约为 45 秒，每隔租约时长的三分之一续租，空闲时每 2 秒轮询 PostgreSQL。可通过 `--lease-seconds` 和 `--poll-seconds` 调整。配置 Redis 时订阅 `travelmind:worker:wakeup`，通知只触发提前轮询，断线时 PostgreSQL 轮询继续。

## 恢复与投影

- 图按 `thread_id=str(task_id)` 保存 `durable-v1` Checkpoint。每个 superstep 使用同步持久化，Worker 先对账已提交的 Checkpoint 及业务游标，再推进下一步。
- 完整 Checkpoint、并行分支的中间写入和提前落库的证据均在实际写入的数据库事务中调用 `check_lease`。失去当前 epoch 后停止执行。
- Agent 执行、证据引用事件、报告审核版本和事件标识来自持久化节点输出。终态及资源事件按冻结的投影批次提交。任务存储维护 SSE 序号和资源可见性。
- 暂停在节点或并行批次之间的安全点生效。澄清问题、答复历史和消费指令 ID 持久化。恢复时使用匹配的 LangGraph 中断 ID，投影确认消费后才推进后续节点。
- 在外部请求已经发出、节点结果还未持久化时崩溃，恢复过程可能重新调用外部服务。升级图版本或 Checkpointer 前应在测试环境验证旧 Checkpoint 可继续读取。
- 当前 `WorkerStore` 不暴露剩余重试预算。可结算的执行异常通过 `AttemptFailure` 交给存储层处理。连续进程崩溃导致租约反复过期时，由任务服务在保留成熟 Checkpoint 对账能力的前提下限制重新执行。

## 验证

从 `backend` 运行 `uv run pytest tests/test_durable_runtime.py`。联调时将 `TEST_DATABASE_URL` 指向专用测试库，应用任务服务的业务迁移后运行 `uv run pytest tests/test_durable_postgres.py`。测试包含投影前崩溃后的重领、旧 epoch 的完整和中间写入拦截，以及澄清、暂停和续跑的跨连接恢复。
