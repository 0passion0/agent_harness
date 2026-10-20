# 能力矩阵与关键差距

本表不评分。**C**＝已通过本轮源码确认（不代表集成实测）；**U**＝未在所选路径确认，不能解读为没有；**D**＝只见文档声明，不能算 C。本轮没有依赖 D 宣称能力。上游 C 的证据与固定版本均见 [source-code-analysis.md](source-code-analysis.md)。

本表保留初始审计基线。后续 G1 已实现退出保存与重连核对后的单次结算/审批恢复，验证结果见 [benchmark-plan.md](benchmark-plan.md)；以下 G1 问题描述指修复前行为。

后续 G2 handler 逃逸异常/取消收尾亦已按用户确认规则实施；G2b 已将 RUNNING/执行登记推迟到全部前置检查通过，65 项定向检查通过，缓存仍先校验授权。下述 G2 源码位置为审计基线，无匹配回执仍是独立后续事项。

## 统一能力矩阵

| 维度 | 当前 Harness | Pi | OpenCode | OpenHands SDK | Deep Agents | mini-SWE-agent | Goose | Pydantic AI |
|---|---|---|---|---|---|---|---|---|
| Loop 可控性 | C 原生图、deadline、interrupt | C turn hooks / abort | U 全 Loop | U 全 Loop | C create_agent 图 | C 显式 step loop | C 定向确认恢复入口 | C 请求/工具预算门禁 |
| Context | C 摘要、参数截短、输出卸载、最终容量估算 | C reserve/summary/文件列表 | C auto/manual/overflow | U condenser | C 原生摘要与卸载 | C 追加 messages；压缩 U | U | C 可选请求前计数；压缩 U |
| 长任务与恢复 | C checkpoint/审批/fork；宿主崩溃记录有缺口 | U 崩溃续跑 | U | C 持久 state/event 重载 | C checkpointer 接入；恰好一次 U | C 逐步存轨迹；续跑 U | C 历史加载/确认恢复 | U durable 后端 |
| 工具可靠性 | C validation/审批/日志/timeout | C 参数校验/截断响应不执行 | C shell timeout/truncated/status | U 全执行链 | C 原生 task/tool | C env.execute/观测反馈 | C 确认待处理识别 | C projected tool budget |
| 状态一致性 | C graph/store/run JSON 分离；无跨系统事务 | U 持久事务 | U | C event 先写后更新索引 | C graph 状态；外部事务 U | C serialized trajectory；事务 U | C SQLite 消息事务 | U |
| 并发异步 | C async streaming/受管资源；并发工作流策略 | C 串行/并行工具批次 | U 本轮未追 | U | C async task | C 所查动作列表串行 | C async stream | C async 请求路径；并发预留 U |
| 沙箱安全 | C 路径/审批；Python 非 OS 沙箱 | U OS 隔离 | U OS 隔离 | U workspace 隔离 | C backend/permissions；OS 隔离 U | U 具体 env | U | U |
| Subagents | C 原生 task/WorkflowContribution | U | U | U | C 独立/分叉消息与结果汇总 | U | U | U |
| 观测调试 | C StreamEvent/日志/用量/回放/LangSmith | C tool/turn events、摘要 usage | C compaction publish、shell metadata | C 持久 EventLog/状态重建 | C 摘要后台历史/task tracing | C save trajectory | C 消息历史与 ACP replay | C RunUsage/UsageLimits |
| 扩展 | C entry points/middleware/skills/MCP/open hooks | C turn/tool hooks | C Effect service/tool plugin | C 事件 callback；其他 U | C middleware/backend/subagents | C query/model/env 边界 | U 全扩展链 | U 全扩展链 |
| 工程验证 | C 本轮本地检查，见 benchmark-plan | U 未运行其测试 | U 未运行 | U 未运行 | C 3 关键文件等同安装版；未运行上游套件 | U 未运行 | U 未运行 | U 未运行 |

未知项多是定向研究的边界，不能据此给项目排优劣。尤其不得将“有 Docker/事件日志/异步 API”推导成“安全、恰好一次、可恢复全部任务”。

## 最优先处理的具体问题

### G1：后端退出未结算宿主运行记录（优先级高，源码确认）

- **触发**：非审批暂停的模型或工具运行期间，Python 子进程异常退出。
- **证据**：`desktop/src/main/backend.ts:139–143` 发 backend.exited；`application.ts:739–742` 只清 running。启动时 `623` 已写 active_run，普通事件的实时 records 主要在内存中，暂停时 `753` 再保存。正常 `finish:818–841` 才保存 runs，且先向 backend 要 snapshot；崩溃分支不能直接复用这个 RPC。`snapshot:412–430` 仅重建 interrupt 运行。
- **影响推断**：最后已提交图消息可能仍在，但当前轮时间线/终态无法通过此恢复入口重建。不能把它描述为 SQLite 全量丢失。
- **测试证据**：现有 application tests 覆盖暂停恢复和正常取消；本轮 17 项通过。还没有 backend 硬杀的确定性复现测试。
- **处置**：保留图/Checkpointer，修改宿主结算与最小运行元数据持久化。先补“后端不可访问时仍结算一次”的测试。

### G2：Workflow 逃逸异常的授权状态收尾（优先级中，待故障注入）

- **触发**：before/after hook、图运行时异常或其他没有转为 ToolMessage 的失败，发生在 verify_execution 已把授权标为 RUNNING 后。
- **证据**：`engine/policy.py:816–825` 启动；`1594–1619` handler 后才 `_mark_dispatch`；`1621–1667` 依赖可提取的 ToolMessage。`common/workflow.py:959–1016` 已处理普通 step Exception，并重抛 GraphBubbleUp，因此不能把普通业务失败与 interrupt 混为一谈。
- **影响推断**：同一选定 run 可留下 active/RUNNING 记录；新 run 有 run-ID 绑定与重新审批路径。尚未证明越权、跨租户执行或必然阻断下一次运行。
- **处置**：先注入 hook 异常、取消和 interrupt，分别定义 failed/paused/cancelled 语义；禁止简单 finally 把所有中断标失败。异常处理契约改变前遵守仓库要求确认方案。

### G3：摘要收益与交付正确性缺少本轮受控对照（优先级高，证据不足问题）

- **现有**：原生摘要/卸载、最终请求估算、结构化评分及 table demo 回放；不是缺少 Context Engineering。
- **证据**：`compose.py:187–212`、`policy.py:57–85`；`evaluation/scoring.py:165–208` 的 completed 只看无错/未中断/有输出。`docs/EVALUATION.md` 记录历史案例质量问题，但本轮未读取原始运行产物，不能将其当新实验结论。
- **触发风险**：长报告、多次写文件、大工具参数、关键数字或原始限制被压缩。缺少固定任务/参数的摘要前后质量数据时，无法声称省 token 且无损。
- **处置**：先 benchmark，候选若失败再改摘要提示/保留策略。不先调一组“更先进”的阈值。

### G4：本地脚本信任边界（部署条件风险，非新发现漏洞）

`python_execution.py:17–22` 明示脚本有宿主用户权限。路径验证、环境筛选、审批均存在；不能推导文件/网络/孙进程不可访问。若使用者会执行不可信脚本，需要真正隔离执行端；可信单用户本地工具无需为模仿其他项目强加远程服务。

### G5：调用次数与费用预算语义不同（方案项，不是当前缺陷）

`tests/test_model_call_limit.py:16–67` 要求超过 30 次调用继续；`scripts/evaluate_table_demos.py:59–79` 的回放预算是脚本专属。产品 deadline、上下文容量、累计 token 和计费上限应分别定义；无需求或测量依据时不新增默认限额。共享预算若实施，必须覆盖摘要/子代理并处理并发预留与缺失 usage。

## 应保留的基础设施

保留 `create_deep_agent`、LangGraph Checkpointer/interrupt、Store 租户命名空间、受管资源生命周期、DomainPlugin 贡献与技能门控、Workflow 图与明确的局部 retry、现有日志/评测。没有源码或测量依据支持框架替换、统一事件总线、通用分布式调度器、全部异步化重写或新增 Plugin 容器。
