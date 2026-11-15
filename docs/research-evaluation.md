# Research Evaluation

离线评测读取文件，复用公共 `EvaluationCase`、`Evidence`、`ReportDraft`、`ResearchResult`、`ExpertResearchResult` 和 AgentEvent v1。评测入口无需启动 API、Worker 或数据库，也无需配置模型密钥。基线采集脚本通过当前工作流的注入入口替换模型与检索，仅用于生成候选快照。

## 数据集与人工核验

[`evaluation_data/cases.json`](../backend/evaluation_data/cases.json) 包含敦煌、泉州各 18 题。题目涉及遗产档案、地理、景点、参观限制、公交接驳及出行时效。每题的 `answer` 按可核验发现记录参考答案、支持状态和来源键。来源目录保留原始链接、段落定位及中文转述。数据集参考日期为 2026-11-15，涉及公告的题目在问题中限定年份或日期。网站内容变化后，可保留旧快照并新建数据集版本。

- `pending` 表示参考答案待人工复核。首批 36 题均处于这个状态。
- `gold` 要求填入 `human_review.reviewer`、带时区的 `verified_at` 和 `notes`。审核人应逐项打开原始出处，核对数字、时间、范围及证据是否支持答案，在备注记录修订或仍需保留的不确定性。
- 待测任务只接收 `brief` 和参考时间。参考发现和参考证据进入评分输入。判读文件绑定候选与参考内容的 SHA-256，快照或参考结论变化后需重新判读。

## 重算 main 基线

在 `backend` 目录中使用仓库锁定的 Python 环境。PowerShell 可将下面的多行参数合并成一行。

```bash
uv run --locked python -X utf8 -m travelmind.evaluation validate --dataset evaluation_data/cases.json

uv run --locked python -X utf8 -m travelmind.evaluation score \
  --dataset evaluation_data/cases.json --runs evaluation_data/main-runs.json \
  --judgments evaluation_data/main-judgments.json --k 1 3 5 \
  --json evaluation-baseline.json --markdown ../docs/evaluation-baseline.md
```

[`evaluation-baseline.md`](evaluation-baseline.md) 由上述命令生成。候选批次固定在本任务开始时的本地 `main`，即 `aedb2b7e2598e2a4e444600e2f7d62f6141d3013`。采集包含三个运行于该提交的固定输入工作流，分别覆盖敦煌遗产问答、泉州审核返工和敦煌检索超时。另从仓库的泉州只读演示抽取既有报告、引用和事件，事后检查遗产题覆盖。示例原本的任务范围更宽。固定输入回放记录本地调用次数与耗时，没有真实模型 Token，也没有真实检索排名。仓库中可访问的真实联调记录只有验收摘要，基线暂时没有可以逐题重放的真实服务快照。

同一批次、参考数据和判读可重算出相同报告。要重新执行固定输入工作流，运行下列命令。新快照会包含新的任务 ID、时间和本地耗时，需要使用新快照生成判读。

```bash
uv run --locked python -X utf8 -m evaluation_data.capture_main --output main-runs-new.json
```

## 接入已有调研任务

将已授权导出的公共 API 记录写入一个 JSON 文件。字段 `task` 为 `TaskRead`，`evidence` 和 `reports` 分别为 `EvidenceRead[]` 和 `ReportRead[]`，`events` 为完整且按序的 `AgentEvent[]`。可增补 `research` 或 `expert_research`、实际观测的 `usage` 和按一次检索返回顺序记录的 `ranked_evidence_ids`。按评测题匹配历史任务，保留原报告、原摘录和原 ID。任务的证据列表顺序及 SSE 到达顺序不足以恢复检索排名，缺失时应省略排名字段。

```bash
uv run --locked python -X utf8 -m travelmind.evaluation import-task \
  --archive exported-task.json --case quanzhou-heritage \
  --source "历史 API 与 SSE 导出" --target-commit <候选完整提交号> --output recorded-runs.json

uv run --locked python -X utf8 -m travelmind.evaluation judge-input \
  --dataset evaluation_data/cases.json --runs recorded-runs.json --output judge-input.json
```

同一候选提交下的多个导出可以合并进一个 `runs` 数组。每项需有唯一任务 ID。独立判读可以由人完成，也可由调用方将评审包提交给独立模型。评审包自带工作说明、输入与输出 Schema。汇总为 `Judgment[]` 后传给 `score --judgments`，可参照 [已签入的判读文件](../backend/evaluation_data/main-judgments.json)。未获得判读时仍可生成确定性检查和运行统计。语义判读中的 `judge_type=human` 只记录候选判读的来源，数据集的 Gold 状态由 `human_review` 管理。

## 比较增强前后

用同一份数据集和相同的 `--k` 分别执行 `score`，再比较两个 JSON 报告。

```bash
uv run --locked python -X utf8 -m travelmind.evaluation compare --before before.json --after after.json --json comparison.json
```

`compare` 按用例和采集方式匹配。输出双方样本数、质量和运行指标，以及双方均有分数时的指标差值。单边覆盖的用例单独列出。用相同输入、深度、供应商配置和判断口径采集重复运行，才能评价真实质量变化。

首次真实配对使用泉州遗产题和相同的深度调研输入。旧图和集成后的新图均通过审核并覆盖两个参考发现。新图增加了模型调用与用时。判读仅覆盖题目所问的两个事实，检索排名及全文评估留空。完整数量和首次新版失败的记录见 [Phase 3 最终集成](../PROGRESS.md#phase-3--最终集成2026-11-15)。

## 指标口径

| 指标 | 计算方式 |
| --- | --- |
| Recall@K | 前 K 个原始排名命中的唯一已知参考来源数 ÷ 参考来源总数。跨快照复用现有 `source_key` 匹配。 |
| Precision@K | 同一命中数 ÷ K。相同来源重复出现只命中一次，短列表剩余名次无命中。 |
| Faithfulness | 全部可核验原子主张中，得到同章节候选引用充分支撑的比例。判读使用正文原句、候选证据 ID 和理由，可接受已证实的替代来源。 |
| Citation Accuracy | 直接支持至少一个同章主张的唯一「章节、证据 ID」对 ÷ 报告中所有这类引用对。另报引用能否解析，方便定位断链。 |
| Completeness | 报告覆盖的参考发现数 ÷ 参考发现总数。逐条保存覆盖、缺失、矛盾或不可判读及相关证据 ID。判读考察时间范围及参考答案中的不确定性。 |
| 任务成功率 | 状态为 `completed` 且留有报告的任务 ÷ 有终态记录的任务。暂停、澄清等非终态单独体现。 |
| 运行统计 | 从单次运行保存输入、输出、总 Token，模型调用次数和墙钟耗时毫秒值。输出观测任务数、合计和均值。存在输入和输出计数才计算总 Token。模型调用统计应由采集端统计实际尝试，耗时起止应在同一批次保持一致。 |

证据检查还报告同源重复记录、专家发现的支持状态和断链。若快照含专家反思和评分卡，则汇总轮次、停止原因、可用等级与未解决问题，并检查调研深度对应的轮次上限及冲突来源去重。工作流检查事件序列、归属、Agent/工具起止、最终审核，以及失败与返工次数。

单题指标保留分子、分母、已判读数量及得分。未采集、语义判读不完整或分母为零时，得分显示为空。明确观测到的空检索结果得 0 分。汇总按候选来源与人工核验状态计算已评分任务的宏平均，并显示样本数。报告覆盖率与任务成功率可独立变化，失败任务也可能留有供评分的报告草稿。
