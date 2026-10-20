# Harness Benchmark 与本轮验证

## G2b 前置检查短路不启动执行（2026-10-20）

本轮只修改 `vertical_harness/engine/policy.py` 和授权生命周期测试：保留授权校验/批准在依赖、缓存、批次与次数检查之前，将 RUNNING 转换及 execution_id 登记推迟到全部检查通过。校验逻辑提取为内部 `_execution_approval`；公共 `verify_execution` 仍按原契约校验并启动，直接工作流调用无需迁移。

| 检查 | 实际结果 |
|---|---|
| 改动前生命周期基线 | 26 通过 |
| 新用例检查旧实现 | 32 个依赖/缓存/重复批次/次数限制组合误留 RUNNING；4 个缓存授权/数据范围拒绝检查通过 |
| 修复后生命周期、原生图及边界检查 | **65 通过**，比 G2 增加 39 项 |
| Python 全量 `.venv/Scripts/python.exe -X utf8 -m pytest -o addopts= -q` | **407 通过、1 项既有附件文案断言失败**；相对 G2 新增 39 项，既有唯一失败不变 |
| 定向 Ruff / diff whitespace | 通过 |
| 验证逻辑提取 AST 比较 | 公共 verify_execution 参数/返回签名不变；原有校验与 run_id 绑定语句 AST 完全一致，仅分离启动动作 |

新增验收：短路不调用 handler，不登记该调用的 execution_id，也不消耗已有批准；后续满足条件的实际执行仍可使用该批准，并正常完成收尾。已经完成的选定 run 在缓存命中后仍为 COMPLETED。同批已有任务真实运行时，重复任务被拒绝不影响其 RUNNING 状态及执行登记。缓存结果仍受用户拒绝与输入范围约束，拒绝时不暴露缓存内容。

授权副作用保持原约定：用户明确批准后仍创建批准记录，配置记住授权时仍记录可信授权；即使随后短路，也不撤销用户已作出的批准。只保证未实际派发的调用不启动，不把已授权误报成正在执行。已批准记录的 run_id 绑定顺序、身份/副作用/数据范围与已拒绝检查保持原样，没有新增异常处理、重试或公共配置选项。

完整旧行为故障输出：`C:/Users/Hello/.codex/references/agent-harness/g2b-before-20261020.log`；修复后定向输出：同目录 `g2b-lifecycle-after-20261020.log`。未处理没有匹配 ToolMessage 的 Command 或 end_run 聚合终态，不宣称覆盖全部状态恢复；没有真实模型质量/费用或外部服务故障收益结论。

全量结果与完整失败输出：同目录 `g2b-pytest-20261020.log`；408 项中唯一失败仍是 `tests/desktop/test_attachment_content.py::test_limits_text_and_preserves_image_content` 的旧 `truncated` 断言。本轮没有改桌面、附件实现、依赖或测试断言来消除该失败。

回滚：撤回本轮私有校验/启动分离与对应新增用例即可，保留此前 G1 和 G2 异常收尾；不迁移存储、不修改历史批准、不重新执行任务。

## G2 执行异常授权收尾（2026-10-20）

用户已明确确认异常规则后，生产变更仅在 `vertical_harness/engine/policy.py`：同步/异步 task handler 抛出的普通 Exception 将对应授权记为 failed，CancelledError 记为 cancelled，随后原样重抛；GraphBubbleUp 不做终态收尾，保留中断与父图控制流。成功/错误 ToolMessage 继续沿原路径收尾，沿用既有单次批准与选定 run 内批准范围，不新增重试、不改授权网关公共接口。

| 检查 | 实际结果 |
|---|---|
| 改动前既有生命周期测试 | 8 通过 |
| 故障注入旧实现 | 8 项同步/异步、普通异常/取消及选定 run 组合留下 RUNNING；原生 ToolNode 不处理异常时另有 2 项复现 |
| 修复后生命周期与原生图集成 | **26 通过**，包含真实 Task.cancel、实际 checkpoint interrupt/resume、ToolNode 处理/传播错误及批准范围检查 |
| Python 全量 `.venv/Scripts/python.exe -m pytest -o addopts= -q` | **368 通过、1 项既有失败**；仍是附件测试期待旧 `truncated` 文案，未改动该产品行为或测试 |
| 定向 Ruff / diff whitespace | 通过 |

可观察验收：已分发任务的失败/取消不再停留 RUNNING；原异常对象保持不变；有选定 run 时沿用同 run 批准，没有选定 run 时下一次执行重新批准；真实图中断恢复只有一次批准；原生 ToolNode 已转换的错误回执仍按原有 failed 路径处理。

完整故障输出：`C:/Users/Hello/.codex/references/agent-harness/g2-lifecycle-before-20261020.log`、`g2-native-before-20261020.log`；修复后定向结果：同目录 `g2-lifecycle-after-20261020.log`。

全量结果与完整失败输出：同目录 `g2-pytest-20261020.log`；本轮 369 项结果相对初始基线增加 18 项授权回归用例，既有唯一失败不变。未改桌面代码，未重复运行 G1 已通过的桌面全套；本轮修改的 policy/test 定向 Ruff 与文档链接/围栏/尾部空格检查通过。

该轮范围限制：只处理 handler 已开始执行后逃出的异常；前置检查短路问题由后续 G2b 修复，没有匹配 ToolMessage 的 Command 仍待单独明确契约。该轮没有修改 end_run 聚合终态、后置验收异常策略、外部副作用补偿或真实服务故障恢复，也没有真实模型完成率/费用收益结论。

回滚：撤回本轮 policy.py 异常收尾及对应回归用例即可；G1 桌面改动独立保留，不修改持久化格式、不清历史状态。

## G1 优化验证（2026-10-20）

生产变更仅在 `desktop/src/main/application.ts`：后端退出先原子保存现有 active_run 的最新记录；会话重连后读取真实 checkpoint，再归档失败或恢复审批。普通完成复用同一记录保存步骤，保持现有协议与 JSON 形状。

| 检查 | 实际结果 |
|---|---|
| 改动前 `npm run test -- tests/application.test.ts` | 17 通过 |
| 新回归用例验证旧行为 | 模型/工具等待时退出及应用重启均未归档；另复现了立即归档方案误判尚未送达的审批通知；修复后通过 |
| 最终 `npm run test`（desktop） | **110 通过，1 跳过**，24 个文件通过、1 个文件跳过 |
| `npm run typecheck`（desktop） | 通过 |
| `git diff --check`（本次产品与测试范围） | 通过 |

新增覆盖：模型/工具等待时退出、重复退出/迟到终态、空闲退出、应用实例重建后的磁盘记录恢复、不自动 run.start/run.resume、审批通知已送达及未送达的恢复与单次完成。`main-backend.test.ts` 另外启动真实 Python bridge 并终止其子进程，验证退出通知；它没有执行真实模型任务。

可观察验收：退出处理期间 RPC 次数为 0；普通失败重连后同 run ID 只有 1 份 failed 记录；持久审批恢复完成后只有 1 份 complete 记录；运行 ID、起始时间与已收到记录保留；重复读取不重复归档。原有正常完成、取消及 snapshot 失败后可继续停止的测试仍通过。

最终桌面测试完整输出：`C:/Users/Hello/.codex/references/agent-harness/g1-desktop-tests-20261020.log`。崩溃时保留 disconnected 通知，终态在用户重连/重新加载会话快照后可见；没有伪造完整快照。Electron 自身被强制结束前尚未保存的增量不能保证恢复；失败记录 elapsed 包含从开始到重连判定的墙钟时间，不能当作纯模型执行时长。没有真实模型任务硬杀、费用/完成率对照或性能收益结论。本次未改 Python，因此未重复运行下述已存在失败的 Python 全套。

回滚：撤回 G1 的 application 与对应测试变更即可，不删除运行目录、不迁移数据库、不重放工具。

## 首轮审计实际执行（优化前）

本轮只新增研究文档，没有修改产品、测试、依赖或既有文档。以下是本轮命令输出，不沿用其他轮次的通过数。

| 检查 | 结果 | 含义与限制 |
|---|---|---|
| `.venv/Scripts/python.exe -m pytest -q` | **350 通过，1 失败**，351 个结果 | 全套本地基线不是全绿；失败完整输出保留在下面的日志路径 |
| `npm run test -- tests/application.test.ts tests/backend.test.ts`（desktop） | **17 通过**，实际仅匹配 application 一个文件 | `tests/backend.test.ts` 不存在；不能声称它通过，已另跑实际 backend 文件 |
| `npm run test -- tests/main-backend.test.ts`（desktop） | **1 通过** | 实际后端定向检查；不是 SIGKILL 恢复测试 |
| `.venv` 实际包版本 | Deep Agents 0.7.28 / LangChain 1.4.6 / LangGraph 1.2.16 | 与声明版本范围区分 |
| 上游/安装版 3 文件 SHA256 | graph / summarization / subagents 全部相等（统一 CRLF/LF） | 只证明所比文件相同，不证明所有依赖一致 |
| 文档检查 | 7 文件、7 个本地链接、围栏闭合、无尾部空格；`git diff --cached --check` 通过 | 31 个远程链接采用固定 Commit，24 个源码锚点已核对本地文件及行号范围；不把 Markdown 检查当运行时验证 |

pytest 失败：`tests/desktop/test_attachment_content.py::test_limits_text_and_preserves_image_content`，在第 51 行期待附件文本包含 `truncated`，实际返回 `Attachment / Path / read_file(offset=0, limit=40)` 的按需读取提示。这是本轮文档变更前已有的工作树行为；没有为使检查变绿而修改测试或产品。不能由这个断言推导附件丢失或图片处理损坏。

完整失败及 warnings 日志：`C:/Users/Hello/.codex/references/agent-harness/audit-pytest-20261020.log`。日志位于工作区外，不随文档发布。项目与依赖仍有 beta/deprecation warnings，本轮未改依赖。

没有新增生产代码，因此没有为文档修改机械运行 Ruff/mypy/build；最终文档范围/链接检查另行记录。没有运行真实模型、付费 Benchmark、故障硬杀、上游测试套件、OS 隔离验证，也没有声称成本、延迟或完成率改善。

## 复用现有评测入口

- `vertical_harness/evaluation/targets.py`：main/plugin/workflow 目标，复用生产装配。
- `evaluation/scoring.py:165–208`：completed、结构化子集、工具尝试与成功回执；`completed` 不等于任务正确。
- `evaluation/offline.py:179–196`：聚合 model calls / input / output / total tokens / duration 与 usage.complete；`compare_reports` 继续作基线比较入口。
- `scripts/evaluate_table_demos.py`：桌面多轮、独立工作区、事件/回执/产物；ConversationBudget 属于此脚本，不是产品默认限额。
- 现有 `docs/EVALUATION.md`：运行边界与人工核查规则；保留它，不再创建平行 runner。

## 指标定义

| 指标 | 分子 / 分母或统计量 | 必须避免的误判 |
|---|---|---|
| 任务完成率 | 满足完整范围和产物质量断言的任务 / 全部计划任务 | 不能用“产生最终文本”替代；超时/预算耗尽保留在分母 |
| 工具调用成功率 | 符合工具结果契约且业务断言通过的调用 / 实际执行调用 | 工具尝试、审批拒绝、Python exit_code != 0、ToolMessage status 分开 |
| 失败恢复率 | 注入失败后满足预期状态且无重复副作用的案例 / 对应注入案例 | 审批 resume 与进程崩溃恢复分层统计 |
| 长任务能力 | 跨多轮/多次摘要后完整完成的任务比例及终止原因 | 不把固定预算提前停止的实验当无损节省 |
| Token / 成本 | root + summary + subagent 全部已报输入/输出、缓存、推理用量 | usage 缺失标 incomplete；无供应商价格不换算金额；失败调用也计入 |
| 执行延迟 | 端到端及模型/工具/摘要的 p50/p95，另列人工等待 | 同时报告成功与失败，不只挑快的成功案例 |
| 上下文增长 | 每次请求实际 token（可得时）与估算、摘要次数、卸载/回读量 | 字符长度不是准确 token；摘要自身消耗也算 |
| 压缩质量 | 必须保留的约束/数字/路径召回及下游任务断言 | 不只评摘要可读性 |
| 状态恢复正确性 | run/thread/身份一致、终态一次、checkpoint 可读、工具不重复 | transcript 可读不能算半完成工具安全续跑 |
| 安全边界 | 明确禁止的操作被拒绝且合法操作正常 | 审批/路径限制和 OS/网络隔离分别验收 |

## 分层任务集与故障注入

以下是**待新增的案例规格**，不是已经跑过的结果。

| 层级 | 案例 | 预期/验收 |
|---|---|---|
| 确定性单元 | fake model 的 32 次工具调用与追问 | 保留现有契约，不能被任意固定调用上限截断 |
| 确定性单元 | fake backend 在 model/tool waiting 发 backend.exited，随后重复通知 | 恰好一个 failed run，清 running，保留身份和已知记录，无 backend snapshot RPC |
| 确定性集成 | 真实临时 Python backend 在受控等待中死亡，再打开当前会话 | 失败记录可见、checkpoint 可读、不重新执行计数工具 |
| 状态边界 | 正常暂停、Python 重启、Electron 重启分别测试；确认后再取消 | 同一审批与 run ID 对应；不能把有效 interrupt 当失败 |
| 授权故障 | 普通 step failure / hook Exception / GraphBubbleUp / cancellation | 分别满足定义的 failed/paused/cancelled 契约；异常不吞掉、不多执行 |
| 工具故障 | 非零退出、120s timeout（测试中缩短等待）、超长日志、取消、孙进程 | 退出码/超时/预览/完整日志一致；直接进程清理与进程树清理分开测 |
| 上下文边界 | 小模型 profile、动态路由、单条大消息、system/tools 很大 | 不发送已知超容量请求；协议配对有效；无法压缩时错误可解释 |
| 长任务质量 | 多次写脚本/修正报告后再跨摘要追问原始约束与关键数字 | 数字/方法/路径/最终产物相符，不仅最终文本像报告 |
| 副作用窗口 | 计数写入完成后，在结果提交前使运行失败 | 不盲目重试或声称成功，未知结果显式可见 |
| 信任边界 | 路径穿越、审批拒绝、无关租户、脚本越权文件/网络 | 前几类用现有策略验收；OS 拒绝仅在隔离执行端启用后声明 |

确定性用例不需要 LLM；优先建立可复现状态断言。真实模型评测仅在隔离测试目录且明确预算下进行，不读取生产会话/凭据，不自动上传 trace。

## 对照实验协议

1. 固定源码快照（含 dirty diff 摘要/所需文件 hash）、lockfile、模型精确标识/provider、推理参数、提示、工具版本、权限、输入文件 hash、任务集和预算。
2. baseline/candidate 使用独立但相同输入的工作区、会话和记忆；外部服务变动与缓存状态记录清楚。先不用生产记忆收益做结论。
3. 先跑确定性故障检查，再运行配对真实任务；随机任务顺序，重复次数按预算预先规定。一次模型输出不能证明架构更优。
4. 记录每个 run 的配置、operation/parent IDs、全部工具回执、摘要调用、usage.complete、终止原因、时间、产物 hash 和独立质量检查。不得仅汇总成功子集。
5. 在看 candidate 结果前设定质量与资源门槛：G1/G2 确定性状态断言必须全过；质量非劣性容忍度与最低样本数先约定，再比较 token/延迟的配对差值和不确定性。
6. 若 provider usage 缺失、输入变更或中途取消，保留结果并分层标识；不能与完整成功实验混算节省百分比。

## 结果记录模板

复用现有报告 rows/usage；需要新增字段时先限定在 benchmark 输出，避免改动产品协议：实验 ID、baseline/candidate revision、任务/input hash、模型/参数、环境、重复序号、run/thread、终止原因、质量断言、工具执行/成功次数、恢复断言、usage 完整性、token/cache/reasoning、延迟、摘要与回读次数、产物 hash、原始 trace 路径。

记录“未测”优于填 0。收益结论要同时附任务质量与资源消耗；改了提示、模型和执行器的实验不能把总改善归因给单一 Harness 机制。
