# 可迁移设计模式

这是设计候选，不是已实现清单。来源的固定版本和源码链路见 [source-code-analysis.md](source-code-analysis.md)；问题编号见 [capability-gap-analysis.md](capability-gap-analysis.md)。每项都以现有 Deep Agents / LangGraph 为承载，收益必须由 [benchmark-plan.md](benchmark-plan.md) 的测量证明。

## 1. 图状态与宿主运行视图各有明确责任

- **真实问题**：G1 的后端退出丢失运行终态，而图消息可能已提交。
- **来源**：OpenHands EventLog 先写事件再更新索引，ConversationState 重载重建视图；Goose 增量持久消息并只恢复已支持的确认状态。
- **适用性与兼容性**：高度适用；LangGraph Checkpointer 继续拥有模型/工具节点状态，Electron 的 `Saved.runs/active_run` 继续拥有运行展示，不复制消息。
- **复杂性/风险**：两个持久化面没有原子事务；必须表示“失败/结果未知”，不能伪造成功、自动重放副作用。`writeAtomic` 的 rename 也不是跨数据库事务或断电 fsync 保证。
- **最小改造（G1 已实施）**：复用已保存的 active_run；backend.exited 先保存最新运行数据，不调用已死亡的 backend snapshot。当前会话重开时核对实际 interrupt，无中断才结算 failed，避免误判尚未送达通知的持久审批。
- **证明收益**：fake backend 退出后只新增一个 failed run；记录 run ID、起始时间；重复退出通知不重复结算；真实进程退出后重开仍可见，工具执行次数不增加。

## 2. 可恢复不等于可重复执行

- **真实问题**：外部写入成功、ToolMessage/Checkpoint 尚未持久化时进程死亡，重放可能重复副作用。
- **来源**：Goose 的 resume 只处理确认状态；OpenHands 持久事件本身并不解决外部事务。
- **适用性与兼容性**：使用原生 `Command(resume=...)`、checkpoint/pending writes，不添加通用“重启后继续全部节点”。
- **复杂性/风险**：业务幂等 key、外部查询、人工确认语义各不相同，不能抽象成万能 retry。读操作与发送/收费/上传不可共享重试规则。
- **最小改造**：先补审批恢复、失败展示及未知结果案例；只有某个业务工具明确支持幂等或查询时，才在该工具局部接入。
- **证明收益**：在“写入已发生但回执未保存”注入失败，计数工具只执行一次，系统不声称任务完成或自动再写。

## 3. 分层上下文，而非删除所有旧消息

- **真实问题**：长工具参数/输出和摘要可能挤掉任务约束或关键数值。
- **来源**：Deep Agents 旧参数缩短、输出/历史卸载和摘要；Pi 摘要保留文件操作及 first-kept 边界；OpenCode 独立 compaction 结果。
- **适用性与兼容性**：当前已实施主要层次，继续使用 SummarizationMiddleware、Backend 与原生消息处理。持久原始证据和模型可见投影分开。
- **复杂性/风险**：摘要可能失真，卸载文件路径可能不可读，过短保留窗口会增加回读；固定字符减少不能直接换算 token 或效果。
- **最小改造**：先准备必须保留的约束/数字/文件路径断言与多轮案例；若失败，在现有摘要参数/提示中局部修改，不新建上下文数据库。
- **证明收益**：同模型同任务下任务通过率不降，关键事实召回不降，再比较含摘要和回读的总 token、延迟。

## 4. Tool Execution Lifecycle 有明确的 paused/failed/cancelled 边界

- **真实问题**：G2 handler 逃逸异常绕过 `_mark_dispatch`；原生 interrupt 又必须保持可恢复。
- **来源**：Pi 工具 start/update/end 和 turn_end；OpenCode 工具结果显式 status/timeout/truncated。
- **适用性与兼容性**：继续用 `SupervisorPolicyMiddleware` 和 Workflow progress/hooks，只对现有授权记录补明确定义的终态；`GraphBubbleUp` 保持框架语义。
- **复杂性/风险**：简单 finally finish 会消费尚待恢复的审批；捕获所有异常可能破坏 LangGraph 中断/取消，也会违反当前异常处理约定。
- **最小改造（G2/G2b 已实施）**：handler 边界将普通异常/取消分别收尾为 failed/cancelled 并原样重抛，GraphBubbleUp 放行；后续分离内部授权校验与启动，保留缓存前授权，只在全部派发检查通过后登记 RUNNING。公共 verify_execution 不变；短路不消耗批准、不影响正在执行的同批任务。无匹配回执仍待单独定义。
- **证明收益**：每种结果下授权状态符合表定语义，同 run/新 run 的审批次数正确，无额外执行、无静默异常吞掉。

## 5. 资源生命周期跟随一次受管运行

- **真实问题**：取消/构建失败/部分 MCP 连接成功时遗留连接，或关闭仍被线程使用的 Store。
- **来源**：当前 `aopen_agent` 的 AsyncExitStack 已完整承担此责任；无需从其他项目替换。
- **适用性与兼容性**：插件 open/aopen、MCP、Store、Checkpointer 保持当前所有权；调用方借用资源仍归调用方关闭。
- **复杂性/风险**：不能跨任务退出上下文；协程取消不保证同步线程/远程调用立即终止。
- **最小改造**：复用既有 MCP 部分失败 cleanup 和取消测试，仅对新增执行端加相同生命周期集成检查。
- **证明收益**：取消和异常后连接关闭一次，Store 关闭晚于在途工作，资源数不随重复运行增长。

## 6. 容量、累计用量和费用分别核算

- **真实问题**：上下文可装入不等于总任务成本可控；缺失 usage 会造成虚假节省比例。
- **来源**：Pydantic AI 请求前/响应后预算；mini-SWE-agent query 前 step/cost/time 检查。
- **适用性与兼容性**：通过现有 callbacks/middleware/RunContext 扩展，不另建模型客户端。当前超过 30 次调用的测试契约必须保留。
- **复杂性/风险**：原生摘要可直接调用模型，单个 wrap_model_call 可能不能覆盖它；子代理/并发会放大超额；硬金额上限还依赖价格、缓存及输出限制。
- **最小改造**：先在 benchmark 测得完整请求归属；若产品确需预算，再定义 opt-in 策略、主/摘要/子代理计费和并发预留，禁止把事后 reported-token 停止线宣传成精确封顶。
- **证明收益**：在阈值附近的并发/摘要测试中可解释每笔用量；usage 缺失标 incomplete；默认长任务行为保持不变。

## 7. Capability 边界落在真正执行工具的一侧

- **真实问题**：文件工具权限不约束 Python 代码自己访问宿主资源；杀直接子进程不证明孙进程已终止。
- **来源**：当前 Python 工具源代码已明确非沙箱。本轮没有审计上游完整沙箱实现，不能虚构一个可直接移植的安全方案。
- **适用性与兼容性**：不可信执行可通过现有 Tool/Backend 扩展点接隔离执行端；保持 file_path、exit_code、timed_out、stdout/stderr preview 与 durable log 路径契约。
- **复杂性/风险**：Windows 进程树、文件挂载、凭据、网络出站、科学库依赖和 artifact 回传都需要真实环境验证；只换 cwd 不成立。
- **最小改造**：需求明确后先针对一个 Python 执行端试点；许可/网络/进程树策略由部署环境提供。不把所有可信插件强行迁出进程。
- **证明收益**：禁止目录/凭据/出站访问均被隔离端拒绝，允许的统计任务及交付物仍正常；超时/取消后无残留进程。

## 8. Trace 驱动的确定性故障检查先于真实模型比较

- **真实问题**：completed 或一次成功 ToolMessage 不能证明领域结果正确，随机模型差异可能被误认为 Harness 收益。
- **来源**：mini-SWE-agent 每步保存轨迹；Pi turn/tool 边界；当前 StreamEvent 与 LangSmith 已能关联 operation/parent/run/thread。
- **适用性与兼容性**：复用 evaluation scoring、offline compare_reports、table demo 回放，不另建评测框架。
- **复杂性/风险**：trace 可能含敏感输入；不同复跑不是同一条外部世界轨迹，checkpoint replay 不撤销副作用。
- **最小改造**：保存 run 配置、任务哈希、工具回执、产物摘要、usage 完整性；先用 fake model + 计数工具覆盖崩溃，再运行隔离工作区内的真实模型配对案例。
- **证明收益**：故障案例稳定复现/通过，质量裁判独立于执行结束状态，费用/延迟结果包含失败与摘要调用。
