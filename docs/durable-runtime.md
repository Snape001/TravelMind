# 独立调研 Worker

独立 Worker 使用 `PostgresWorkerStore` 领取任务、续租、读取指令和提交业务投影。

## 本地启动

启动与升级命令见 [README](../README.md#本地启动)。Worker 进程可独立重启或启动多个实例。默认租约为 45 秒，每隔租约时长的三分之一续租，空闲时每 2 秒轮询 PostgreSQL。可通过 `--lease-seconds` 和 `--poll-seconds` 调整。配置 Redis 时订阅 `travelmind:worker:wakeup`，收到通知时提前轮询。Worker 关闭时取消监听及心跳，并释放数据库和模型连接。

## 恢复与投影

- 图按 `thread_id=str(task_id)` 保存 Checkpoint。新任务使用 `durable-v2`，存量任务按 Checkpoint 中的图版本恢复。每个 superstep 使用同步持久化，Worker 先对账已提交的 Checkpoint 及业务游标，再推进下一步。
- 完整 Checkpoint、并行分支的中间写入和提前落库的证据均在实际写入的数据库事务中调用 `check_lease`。失去当前 epoch 后停止执行。
- Agent 执行、证据引用事件、报告审核版本和事件标识来自持久化节点输出。终态及资源事件按冻结的投影批次提交。任务存储维护 SSE 序号和资源可见性。
- 暂停在节点或并行批次之间的安全点生效。澄清问题、答复历史和消费指令 ID 持久化。恢复时使用匹配的 LangGraph 中断 ID，投影确认消费后才推进后续节点。
- 在外部请求已经发出、节点结果还未持久化时崩溃，恢复过程可能重新调用外部服务。升级图版本或 Checkpointer 前应在测试环境验证旧 Checkpoint 可继续读取。
- 任务服务随领取结果返回尝试计数、尝试上限和本次租约的执行许可。最后一次执行租约可正常运行。仅投影租约先补齐已提交 Checkpoint 的报告、事件、终态或暂停及澄清中断。后续仍需推进图时，Worker 以 `ATTEMPT_BUDGET_EXHAUSTED` 结算失败。

## 验证

从 `backend` 运行 `uv run pytest tests/test_durable_runtime.py`。联调时将 `TEST_DATABASE_URL` 指向专用测试库，应用任务服务的业务迁移后运行 `uv run pytest tests/test_durable_postgres.py`。测试包含投影前崩溃后的重领、旧 epoch 的完整和中间写入拦截，以及澄清、暂停和续跑的跨连接恢复。

重试预算回归覆盖最后一次执行租约、仅投影租约的终态与中断恢复，以及只有未提交中间写入时的耗尽结算。

Phase 3 测试覆盖反思轮次和调度决策恢复、证据与 Agent 事件投影，以及旧图的续跑。 [专家反思](agent-runtime.md#专家反思)
