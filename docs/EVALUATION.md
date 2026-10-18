# LangSmith 质量闭环

`required_tools` 保留“模型尝试调用”的历史指标；需要成功执行回执时使用 `required_successful_tools`，它要求匹配成功 ToolMessage。成功回执不证明领域事实正确，仍需结构化参考断言。执行目标同时受调用 deadline 限制，见[稳定化说明](STABILIZATION.md)。

本模块用 LangSmith SDK 运行实验、存储反馈与数据集，OpenEvals 调用评审模型，LangMem `prompt_memory` 生成一次候选提示词。普通对话没有评测调用；在线评测在独立 worker 中执行。

```mermaid
flowchart LR
    A[主智能体与插件] --> B[LangSmith Trace]
    B --> C[在线稳定抽样评测]
    B --> D[用户评分与明确修正]
    D --> E[Regression Dataset]
    E --> F[离线基线评测]
    F --> G[生成候选提示词]
    G --> H[独立验证集比较]
    H --> I[保存候选和报告供人工选择]
```

## 配置与运行边界

| 配置 | 默认值与用途 |
| --- | --- |
| `LANGSMITH_TRACING` | `false`；CLI / `aopen_agent` 显式读取 `.env`，开启后上传对话与工具轨迹 |
| `LANGSMITH_API_KEY` / `LANGSMITH_ENDPOINT` / `LANGSMITH_PROJECT` | LangSmith 连接；project 默认 `vertical-research-agent` |
| `VA_MODEL` / `VA_API_KEY` / `VA_BASE_URL` | 执行目标的模型和连接 |
| `VA_EVAL_MODEL` | 可选评审模型；默认 `VA_MODEL` |
| `VA_OPTIMIZER_MODEL` | 可选优化模型；依次回退到 `VA_EVAL_MODEL`、`VA_MODEL` |
| `VA_EVAL_CONCURRENCY` | `2`；实验并发上限，范围 1–32 |
| `VA_ENABLE_ONLINE_EVALUATION` | `false` |
| `VA_ONLINE_EVAL_SAMPLE_RATE` | `0.1`；按 run UUID 的稳定哈希抽样，范围 0–1 |
| `VA_ONLINE_EVAL_POLL_SECONDS` | `60`；在线轮询间隔，最小 10 秒 |
| `VA_ONLINE_EVAL_LOOKBACK_HOURS` | `24`；每轮查询的回看窗口 |
| `VA_ENABLE_FEEDBACK_REGRESSION` | `false`；提交修正反馈后自动入库，online worker 同时补录窗口内修改过的反馈 |
| `VA_REGRESSION_DATASET` | 启用反馈自动入库时必填；示例 `vertical-regression` |

新增模型角色均通过现有 `init_configured_model` 初始化，没有额外的模型供应商配置。动态路由若已启用，目标智能体继续遵循现有质量路由；评审和优化使用独立角色配置。LangSmith 凭据与模型凭据分开使用。

不填写 `--upload` 时，本地 JSONL 实验不会访问 LangSmith，目标、评审和优化 trace 均不上传；使用远程数据集会读取 LangSmith。LLM 仍访问 `VA_BASE_URL` 对应的模型服务。`--upload` 会创建内容寻址的数据快照及实验，上传案例、结果和 trace；快照方便基线与候选使用相同参考数据。完整报告和优化目录可能包含对话数据，应放在工作区私有目录。

在线查询和反馈读取采用 SDK 的新 `client.runs.query/retrieve` 接口，需要支持 v2 runs API 的 LangSmith 后端（至少 0.16.0）。每个 project 只部署一个 worker；反馈 UUID 确定性去重，但多个 worker 同时评审同一 run 仍可能重复消耗模型调用。查询自动分页，默认仅评测窗口内**开始且已完成**的 run；长任务及长时间停机后按需扩大窗口后执行 `--once`。默认项目需与生产 tracing project 相同。不要将 LangSmith 的服务端在线规则配置为重复执行同一评审。

## 离线数据与主智能体/插件评测

JSONL 每行一个案例，只有 `inputs` 必填：

```json
{"inputs":{"prompt":"请核查：正文引用 [1] 和 [3]，文献表只有 A、B"},"outputs":{"output":"正文引用 [3] 越界，文献 [2] 未被引用。"},"metadata":{"category":"citation-boundary"}}
```

`inputs` 接受单个非空 `prompt`，或非空 `messages` 列表；拒绝额外的内部状态字段。直接工作流也通过 messages 协议运行。`outputs` 是参考答案，可包含自然语言 `output`、结构化结果 `structured_response` 及 `required_tools`。结构化参考是递归字段子集匹配，数组严格匹配；工具断言检查实际调用名，`task:citation_audit` 可检查具体委派。自然语言参考由评审模型判断语义一致性，允许等义表达；有文本参考时不能用 `--no-judge`。

```bash
# 完整主智能体（所有已启用插件）
vertical-eval run --data train.jsonl --target main --report .agent-workspace/main-report.json

# 只启用指定插件的监督者，验证插件指令与调度
vertical-eval run --data plugin-cases.jsonl --target plugin:research.citations --report .agent-workspace/plugin-report.json

# 直接验证只读确定性插件，不调用执行模型或评审模型
vertical-eval run --data examples/evaluation/citations.jsonl --target workflow:citation_audit --no-judge --report .agent-workspace/workflow-report.json

# 从回归集读取；加 --upload 才上传新实验
vertical-eval run --dataset vertical-regression --report .agent-workspace/regression-report.json --upload
```

新报告不覆盖已有文件；失败案例或评审异常导致 `run` 返回非零退出码，便于接入 CI。`completed` 检查执行是否正常完成，`reference_match` 检查结构化参考，`required_tools` 检查工具调用，`quality` 是 OpenEvals 的 0/1 评审分。没有参考答案的在线/离线模型分只表示可观察的回答质量，不代表事实已核实。

每条目标输出及报告汇总增加 `usage`：`model_calls`、`input_tokens`、`output_tokens`、`total_tokens`、`duration_ms` 与 `complete`。逐案例还保留供应商上报的缓存/推理计数和 `usage_reported_calls`。`complete=false` 时 token 只是已知小计；异常调用或缺少用量元数据不能视为免费。耗时汇总是各案例耗时之和，包含构建和关闭资源，不是并发实验的墙钟时间。评审与后台 worker 的开销不计入目标用量，`usage` 不发送给质量评审。

比较报告返回 `usage_deltas`（候选减基线）；只有两边统计完整时才计算。原有质量改善条件继续有效；逐案例与平均质量均无退步、任务集匹配且总 token 降低的候选，也可标记为 `review_candidate`，仍不修改生产配置。远端子任务内部和供应商隐藏重试的计费须另行核对，详见 [Token 优化说明](TOKEN_EFFICIENCY.md)。

目标每条案例使用新的临时目录、内存 store/checkpoint 和隔离身份，不读取或更新生产记忆。工具审批仍然有效，不自动批准；直接工作流只接受无租户限制的只读 runnable。插件及 MCP 仍是受信任执行代码，评测不是 OS sandbox；可能有副作用的集成应连接测试环境。`workflow:*` 不重放 Supervisor 的依赖调度策略，需要验证调度时使用 `main` 或 `plugin:*`。

## 候选提示词与独立验证集

```bash
vertical-eval optimize --target main --data train.jsonl --validation-data heldout.jsonl --output-dir .agent-workspace/main-candidate
vertical-eval optimize --target plugin:research.citations --dataset plugin-training --validation-dataset plugin-heldout --output-dir .agent-workspace/plugin-candidate --upload
```

流程是训练集基线 → 一次 LangMem 提示词优化 → 验证集基线 → 同一验证集候选。训练和验证输入不能重叠；`prompt` 与等价 `messages` 也会被识别为重叠。优化器只看到训练结果，不看到验证案例。输出目录必须为新目录，包含案例快照、基线/候选提示词和 `report.json`。

主智能体优化 `BASE_PROMPT`；插件优化该插件的 `PluginContribution.instructions`，保留固定监督者策略和其他插件配置。示例 citation 工作流内部是确定性代码，没有可优化模型提示词；用直接工作流评测定位逻辑问题。候选仅通过显式 `prompt_overrides={"main": text}` 或 `{"research.citations": text}` 在评测中注入，不写 `.env`、源码或生产提示词。推荐需要执行无异常、指标改善且没有逐案例退步；小样本结果仍需人工复核。

## 用户反馈 → Regression Dataset

先为对话分配可关联的 UUID（也可以从应用日志取 run ID）：

```bash
vertical-agent "核查引用" --run-id 00000000-0000-4000-8000-000000000001
vertical-eval feedback --run-id 00000000-0000-4000-8000-000000000001 --score 0 --comment "遗漏了越界引用"
```

此 run 必须存在于配置的 LangSmith project。`score` 范围 0–1；评论最多 10,000 字符。模块提供 Python `UserFeedback` / `submit_feedback`，便于应用提交；应用后端仍须验证当前用户有权给该 run 提交反馈，不能把 LangSmith 服务凭据交给终端用户。

明确修正答案写成 JSON，例如 `correction.json`：

```json
{"output":"正文引用 [3] 越界，文献 [2] 未被引用。","required_tools":["task:citation_audit"]}
```

主智能体通常返回自然语言；直接工作流明确返回结构化数据时，可用 `{"structured_response":{"status":"issues"}}` 作为对应参考。

```bash
# 开启 VA_ENABLE_FEEDBACK_REGRESSION 后，带修正的提交会自动入库
vertical-eval feedback --run-id 00000000-0000-4000-8000-000000000001 --score 0 --comment "应报告越界" --correction correction.json

# 未启用自动入库时，按上一命令返回的 feedback_id 显式转换
vertical-eval regression --feedback-id 00000000-0000-4000-8000-000000000002 --dataset vertical-regression

# 批量补录最近一周修改过的带修正反馈，可由系统计划任务周期运行
vertical-eval sync-feedback --dataset vertical-regression --hours 168
```

转换保留来源 run、feedback ID、用户评分和评论；模型评测使用原始调用输入和明确修正答案。单纯好评/差评和原来的错误输出不会成为参考答案。相同 feedback ID 重试导入只保留首个快照，修订既有案例需在 LangSmith 数据集中明确编辑。导入本身不做随机拆分：使用者应把训练集和 held-out 回归验证集分开管理。

开启在线 worker：

```bash
vertical-eval online
# 或由系统调度器定期调用
vertical-eval online --once
```

worker 对主智能体 `vertical-agent` 和启用插件的工作流名称进行抽样评分，保存版本化 `va_online_quality_*` 模型反馈；自身评审关闭 tracing，避免递归评测。已完成评分先通过 feedback stats / feedback 读取跳过，不重复调用模型。若同时启用反馈回归，worker 也会同步最近窗口内新增或修改过的修正反馈。
