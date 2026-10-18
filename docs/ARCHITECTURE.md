# Deep Agents / LangChain / LangGraph 可插拔领域 Harness 设计规范

**设计基线**：Deep Agents / LangChain / LangGraph 原生组合。稳定化实现和升级边界以 [STABILIZATION.md](STABILIZATION.md) 为准，部署解析见 uv.lock；最低适配版本为 Deep Agents 0.7.28、LangChain 1.4.6、LangGraph 1.2.16。设计建议不代表全部已实现。

## 1. 核心职责划分

| 子系统 | 框架现成能力 | 核心自有责任 | 不要造的轮子 |
|---|---|---|---|
| Agent 运行 | `deepagents.create_deep_agent` | 装配参数与版本测试 | 自定义 ReAct 循环 |
| Plan | `TodoListMiddleware` | 复杂任务启用条件 | 另写 Todo 执行器 |
| Tool | `langchain.tools.tool`, `BaseTool` | Pydantic args schema / 授权 / 幂等 | 自己实现 JSON tool calling |
| Skill | Deep Agents `SkillsMiddleware` | 专业 SOP、SKILL.md、插件资源 | 另写 Skills 路由系统 |
| Context | `SummarizationMiddleware`、offloading、`ContextEditingMiddleware` | 不可摘要的证据单独存储、摘要质量测试 | 自己截断对话历史 |
| Delegation | `subagents`、`CompiledSubAgent`、async subagents | 专业职责、子任务验证 | 自己拼 supervisor 协议 |
| Model | `init_chat_model`、`wrap_model_call`、`ModelFallbackMiddleware` | 模型分层/预算/能力测试 | 自己写多家模型适配 API |
| HarnessProfile | `HarnessProfile`、`register_harness_profile` | 按 provider/model 适配提示/工具可见性 | 将业务规则写进模型 profile |
| Files | State/Filesystem/Store/Composite Backend | workspace ACL/租户隔离/对象存储适配 | 重写虚拟文件工具 |
| Durable | LangGraph Checkpointer、interrupt/Command、Store | 数据库生命周期、重试幂等、恢复测试 | 自制轮询恢复状态机 |
| HITL | `interrupt_on`、human-in-the-loop middleware | 鉴权、可审查审批 UI、后端业务授权 | 自制模型确认对话流 |
| Testing | `pytest`、LangSmith Dataset/Evaluators | 任务基准、golden data、领域验证器 | 自写完整 tracing/eval 平台 |
| Observability | LangSmith tracing、tags、metadata | 租户日志脱敏、成本/时延 KPI | 自写 LLM trace viewer |
| Connectors | LangChain MCPAdapter（beta） | 领域认证、服务配置、限流 | 自己写 MCP 协议 |
| Interface | Streaming/ACP/AG-UI 等现成协议 | 产品 UI 与业务审批 | 重新设计 Agent 通讯协议 |

MCP 接入由 `config.mcp` 校验 `VA_MCP_SERVERS`，`engine.mcp.aopen_mcp_tools` 使用显式 `StdioTransport` / `StreamableHttpTransport` 与 LangChain 原生 `MCPAdapter` 发现工具。每个服务独立会话，工具加服务名前缀后作为 `ToolContribution` 进入现有去重与 HITL 注册流程；副作用级别由宿主配置，默认 `external`。`aopen_agent` 在同一异步任务内持有会话与缺省存储，退出时反向关闭；stdio 关闭跨上下文 keep-alive，避免遗留子进程。CLI 在启用 MCP 时使用此异步入口，Time Travel 同时使用异步持久化 API。纯监督者模式不连接 MCP，查询检查点历史不执行 MCP 发现。

Tool Annotations 直接复用 `MCPAdapter` 的 `metadata.mcp.tool.annotations`（snake_case），通过 `ToolContribution.mcp_annotations` 读取并在 HITL 请求描述中展示。工具命名空间重命名保留原生元数据。注解只是服务端提示，宿主 `effect` 继续独立控制审批；`idempotent_hint` 不作为自动重试或去重依据，缺省注解不自行补全。

## 2. 六个模块组（推荐目录边界）

1. `composition`：应用唯一装配点；扫描分发包入口、校验清单、聚合 LangChain 原生对象，最后调用一次 `create_deep_agent()`。
2. `contracts`：Pydantic `BaseModel` 校验配置、PluginManifest、领域工具输入输出、VerifierReport、DomainArtifact；LangGraph runtime context 使用 SDK 支持的 dataclass/TypedDict。
3. `profiles`：模型构建/供应商适配；模型级 HarnessProfile 通过注册表统一配置，避免插件对全局 Profile 随意覆盖。
4. `runtime`：LangGraph persistence/store/sandbox/permissions/HITL/streaming；生产级数据库生命周期由应用进程管理。
5. `plugins`：**不是**主包子目录，而是由第三方 Python distribution 提供的 `vertical_agent.plugins` Entry Points；支持 Tool、Skill、Subagent、Middleware、domain verifier、connector。
6. `quality`：业务验证、LangSmith Datasets、Trace 分析、CI Regression；领域插件提供自己的 golden cases 和专用评分器。

## 3. 七种关键设计模式

| 模式 | 何时用 | 如何实现 | 设计约束 |
|---|---|---|---|
| Dynamic Model Routing | 不同任务具有不同成本/能力约束 | `@wrap_model_call` + `request.override(model=selected_model)`；主代理/子代理也可明确各自模型 | 使用稳定信号（任务类别、上下文预算、工具调用后状态）而非仅消息数；测试 reasoning/tool/structured output 能力 |
| Context Engineering | 长任务、高工具输出量 | 默认 summarization/offloading、可选 `ContextEditingMiddleware`，`AGENTS.md` memory，文件化 Artifact | 引证/数据值等关键信息不可只存易损摘要；脱敏；原始证据单独可追溯 |
| Progressive Skills | 领域工具/SOP 多 | `SKILL.md` + `SkillsMiddleware`，v0.7.22+ `metadata.include_tools` 做按需工具暴露 | `include_tools` 必须空格分隔**字符串**；使用专属稳定工具名；需要权限的写工具先走 eager+HITL |
| Subagent Isolation | 具有独立上下文、专用工具的复杂任务 | `subagents=[{name,description,system_prompt,tools,model}]` 或 `CompiledSubAgent` | 每个 Subagent 精简权限；最终主代理只收经结构化校验的摘要；不必每个 Skill 都是 subagent |
| Harness Profiles | 不同 provider/model 的 harness 行为差异 | `register_harness_profile(provider:model, HarnessProfile(...))` | 属于 beta；初始化时注册；全局统一所有权；不要误用为插件注册器或任意 task router |
| Durable Execution | 长流程、中断审批、断点恢复 | 持久化 Checkpointer + 稳定 `thread_id` + LangGraph `Command(resume=...)` + `Store` | checkpoint 不等于外部 API 恰好执行一次；副作用要幂等键、数据库事务/outbox |
| Evaluator–Optimizer | 迭代可靠性/成本/任务通过率 | LangSmith trace -> dataset -> custom code/LLM-as-judge evaluators -> 实验比较 -> 版本回滚 | 不是默认的内建自优化循环；验证结果不要由单一生成模型自评作为唯一依据 |

另建议四个辅助设计：Tool Discovery（Skills+LLM tool selector），Policy-as-Code（权限/批准/租户数据访问），Structured Outputs（Pydantic `response_format=`），Saga/Compensation（外部多系统写入失败时补偿）。

## 4. Agent Loop 与工具行为

执行链：`User -> app validation -> Deep Agents -> model(tool calls) -> auth/RBAC -> domain tools -> Pydantic output -> artifact/evidence -> verifier -> final response -> trace/eval`。

外部副作用：**永远不要**假设 checkpoint 或重试让写入“恰好一次”；工具后端应接收 `(tenant_id, request_id, operation_id, idempotency_key)` 并对重复请求返回同一资源标识。读工具可有限次数退避重试；写工具需要幂等且受 HITL 审批；权限不能用提示词代替。`ToolRuntime[Context]` 注入调用者身份，不能作为模型可填写参数。`FilesystemPermission` 仅适用于内建 filesystem 工具，不能直接保护自定义工具或任意 shell 命令。

## 5. 三类状态必须分离

- **Thread state/checkpoint**：短期消息、agent plan、结构化 state，按 thread_id 保存。
- **Long-term store**：跨线程项目偏好、经过确认的专业记忆、操作策略；按 tenant + project namespace。
- **Artifacts/evidence**：PDF、原始解析结果、数据表、引用证据、验证报告，在对象存储或项目空间存储。使用不可变版本/hash 和 provenance；在模型上下文中只保留引用与摘要。

LangGraph runtime context 是调用级静态配置；官方 `context_schema` 示例支持 dataclass/TypedDict，而不是将 Pydantic BaseModel 直接当作每类 LangGraph State 使用。Pydantic 在输入输出边界验证；LangGraph State 保留框架预期 reducer/序列化语义。

## 6. Skills 与插件生命周期

- 插件 wheel 中自带 `SKILL.md`、参考资料、专业脚本、工具定义和评测集。
- 独立 `pyproject.toml` 的 entry point：`[project.entry-points."vertical_agent.plugins"]`。
- 生产配置显式 allowlist；扫描时拒绝缺失插件、重复 ID、工具名冲突和相同子代理名。
- 安装 wheel 后**重启应用进程**完成发现；这叫代码解耦与运行时组合，不代表不部署/不重启的热插拔。
- 插件应遵循稳定的契约版本，并在主包/插件之间遵循 semver 区间。
- Python Entry Point 是任意代码加载，不是安全隔离。未信任第三方逻辑放 MCP server / sandbox 微服务运行，主程序只暴露经过授权的适配器。
- 配置/特性开关适合运行时选择启用；安装/删除插件为发布管理行为。
- 同一个 Agent 的 Middleware **有顺序**；内置 Middleware 根据名字合并/替换，禁止未知插件随意用核心名字覆盖内置实现，建议静态策略验证。

## 7. 科研审查领域示例

建议先支持：`pdf-ingest`（PDF/LaTeX 定位与证据锚点）、`citation-audit`（索引与原文核验）、`consistency-check`（数值表/图一致性）、`methodology-review`（方法统计约束）、`reproducibility`（沙盒重跑）。

插件职责：

- 引文 Tool：编号引用提取、DOI 元数据查询、原始论文页面定位、claim/passage 核对。
- Skill：先定位输入，再查引文与来源，然后判断冲突，最后输出可信等级与证据。
- Domain Schemas：`EvidenceRef{document_id,page,span,quote,hash}`、`Finding{type,severity,claim,evidence,status}`、`AuditReport{findings,limitations,...}`。
- Verifier：确定性校验（索引范围、数字表格、DOI 匹配） + LLM 辅助语义判断 + 人审。
- 合规要求：不要把“来源无法获得”映射成“引用造假”；区分 `verified`, `suspected`, `insufficient_evidence`。

当前示例仅提供 `research.citations` 作为最低可行包级扩展展示，**没有**完整的科研事实核验能力。

## 8. 生产基线

- Workflow：定义 `task_success`、`groundedness`、`critical_error`、`approval_violation`、`human_intervention`、`per_task_cost`、`p95_latency`。
- 可靠性：50–100 个标注任务集 + 插件契约测试 + 执行轨迹回归 + 重试/取消/恢复测试。
- 安全：密钥管理、租户隔离、最小权限、HITL、安全沙盒、工具层审计、PII 脱敏、输出来源证明。
- 生产持久化：Postgres Checkpointer (由宿主生命周期提供)、LangGraph Store/独立数据库、外部对象存储。
- 运维：工具超时、速率限制、预算限制、模型 fallback、应急关闭插件、schema/manifest 版本发布策略。

## 9. 应用日志实现

`vertical_harness.logging` 管理标准 logging 输出与 `ContextVar` 调用关联上下文，所有日志配置集中在 `HarnessSettings`。CLI 显式初始化 root logger；框架、插件均使用各自模块 logger，日志配置不依赖具体领域包或智能体名称。

`engine.callbacks.WorkflowLoggingHandler` 使用 LangChain 原生 callbacks 观察执行树，支持同步/异步、子智能体与并发工具。每次运行独立实例，通过 `RunnableConfig.callbacks` 传递；用框架提供的调用 ID 建立父子关系，用单调时钟计算耗时。它不实现执行循环、不修改消息、工具结果、审批或重试行为。框架挂起与取消分别记录，避免混同业务失败。

INFO 记录应用生命周期与业务结果统计，DEBUG 展开完整阶段和执行树。事件字段由调用方通过 `extra={"event_fields": {...}}` 显式提供，基础设施不绑定领域字段；异常仅保留类型与栈位置。输入、输出和凭据不进入项目事件，原始外部库日志仍遵循相应库策略。该模块补充 LangSmith tracing，不替代审计服务或 LLM trace 存储。

具体配置及库集成示例见 README 的“日志”一节。

## 10. Deep Agents Supervisor + LangGraph Workflow Plugins 架构

针对确定性业务工作流监督场景，框架采用 **Supervisor + Plugin Registry + LangGraph Workflow Plugins** 三层解耦架构，原生对接 Deep Agents 的 `CompiledSubAgent` 与 `AsyncSubAgent`，无需将每个 LangGraph 工作流手动包装为普通 `@tool` Adapter：

- **Supervisor（主智能体）**：唯一负责理解需求、制定计划、委派已注册工作流、查询状态与验收结果的智能体，不直接操控工作流内部节点。
- **Workflow Plugin Registry（插件工作流注册表）**：通过 `WorkflowContribution` 与 `discover_workflow_graphs` 维护工作流清单、模式切换（`compiled` / `async`）及权限策略，不引入额外 LLM。
- **Workflow Plugin（纯 LangGraph 工作流）**：各插件导出独立的预编译 `StateGraph`，内部可完全由确定性节点组成。

### 10.1 工作流三层数据契约（强制校验）

每个 `WorkflowContribution` 必须显式定义并区分三层状态 Schema（由 `WorkflowContribution` 在注册期强制校验）：

| 状态层 | 字段 / 别名 | 职责与安全边界 |
|---|---|---|
| `InternalState` | `internal_state` | 定义工作流内部节点流转使用的数据（如 `validated_input`、`evaluation_result`），禁止外部调用方或 LLM 直接写入，也不得泄露到最终输出 |
| `input_schema` | `input_schema` | 定义调用方/Supervisor 需要提供的数据（继承 `WorkflowInputState`，兼容 `messages` 协议），由入口节点统一完成参数提取与 Pydantic 校验 |
| `output_schema` | `output_schema` | 定义工作流最终返回给调用方/Supervisor 的数据（继承 `WorkflowOutputState`，包含 `messages` 与 `structured_response`） |

### 10.2 双运行模式（`CompiledSubAgent` vs `AsyncSubAgent`）

- `WorkflowMode.COMPILED`（默认）：通过 `CompiledSubAgent(runnable=...)` 在单进程内挂载预编译图，自动继承父图 Checkpointer 并绑定 `durability="sync"`，适合短工作流与强确定性同步验收。
- `WorkflowMode.ASYNC`：通过 `AsyncSubAgent(graph_id=...)` 对接 Agent Protocol（支持 `start_async_task`、`check_async_task`、`update_async_task`、`cancel_async_task`、`list_async_tasks`），适合长耗时任务与并行生命周期管理。

### 10.3 确定性监督策略层（`SupervisorPolicyMiddleware`）

由 `SupervisorPolicyMiddleware` 与 `build_workflow_interrupt_configs` 在 `Dispatch / Monitor / Validate` 阶段执行确定性治理：

1. **插件白名单与租户权限（RBAC）**：拦截未注册工作流及未授权租户（`allowed_tenants`）。
2. **任务依赖关系（`depends_on`）**：基于当前会话消息历史校验前置工作流是否已成功完成。
3. **最大委派/重试次数（`max_dispatches`）**：防止主智能体对同一工作流无限循环重试。
4. **幂等性复用（`idempotent`）**：对相同工作流与任务载荷直接复用已验收结果。
5. **结果模式校验（`result_schema`）**：对工作流返回 JSON 执行 Pydantic 确定性校验。
6. **高风险任务审批（HITL）**：利用 `InterruptOnConfig(when=...)` 仅在委派 `WRITE` / `EXTERNAL` 工作流时触发人工审批。

### 10.4 智能体与工作流 Time Travel / Time Travel Fork（`durability="sync"`）

- 默认启用 `durability="sync"`，确保主智能体与子工作流在每一步节点完成后同步落盘检查点（支持 `SqliteSaver` / `AsyncSqliteSaver`）。
- 提供 `get_checkpoint_history`、`get_checkpoint_snapshot`、`fork_checkpoint` 与 `time_travel_invoke`（及对应异步接口），支持按历史 `checkpoint_id` 原地回放、同线程状态修改分叉（`--update-state`）或跨线程克隆分叉（`--fork-thread-id`）。
- 由于 `SupervisorPolicyMiddleware` 直接从当前检查点的 `state["messages"]` 重建调度计数与完成状态，执行 Time Travel 回溯或分叉后，监督策略状态自动与回溯点保持严格一致。

## 11. 用户记忆与独立后台整合

`engine.memory` 复用 LangMem `create_memory_manager`，以 Pydantic `UserPreferences` / `ConsolidatedMemory` 作为有界档案，通过原生 LangChain middleware 接入同步和异步 Deep Agent。既有 `/memories/` 文件路由仍供工具读写，不自动注入文件内容。`enable_memory` 是总开关，自动偏好和后台整合分别选择启用；检查点生命周期独立。

新自动记忆按可信运行上下文的 tenant/user 摘要隔离，包含偏好、长期事实、不可逆哈希回执，以及临时输入队列。偏好在回答前更新；整合由独立 `--memory-worker` 进程每分钟检查持久化周计划，默认从每个用户首次采集起每 7 天处理一次。任务不依赖对话进程存活，重启后续跑；没有新输入不消耗模型调用。`--consolidate-memory` 提供系统调度器单次执行入口。

每批输入有大小限制；LangMem 更新单个结构化档案，Store `batch` 将摘要与队列消费一并提交。成功队列原文删除，仅保留摘要和去重回执；失败批次保留待重试，成功批次不重复运行。只提取用户明确陈述，避免固化模型猜测，并将召回内容标为低信任参考。新命名空间与原有文件记忆共存，不迁移或删除旧记忆。API 及配置、数据保留说明见 README。

当前调度面向单 worker、共享 SQLite；宿主须串行化同用户偏好写入，并对线程身份访问负责。多副本需要分布式租约及写冲突控制。Store 不参与检查点回滚，不能将 Time Travel 解释为删除已学习的用户记忆。

## 12. 独立评测、反馈与回归数据

`observability` 只负责 LangSmith 配置和 tracing 生命周期，不负责模型配置。`evaluation` 独立包通过既有 `build_agent` / `aopen_agent` 与插件发现契约适配评测目标，`vertical-eval` 只解析参数与调用服务，不向对话主流程加入评测逻辑。`targets` 管隔离运行和候选注入，`scoring` 管确定性断言与 OpenEvals，`offline` 管 LangSmith 实验及 LangMem 候选比较，`feedback` 管用户反馈和参考案例转换，`datasets` 管固定快照，`online` 管独立 worker。配置和 Python 公共接口位于该包，模型初始化复用 `VA_*`。

生产 trace 使用可关联 run UUID；在线抽样评测写入模型来源的版本化反馈，用户反馈单独以 `user_feedback` 保存。明确修正答案才提升为 Regression Dataset 的参考输出；来源 run / feedback UUID 保留，重复导入保留首个案例。离线优化从训练反馈生成候选，用同一 held-out 快照比较并输出逐案例回退，不修改生产配置。详细命令与边界见 [EVALUATION.md](EVALUATION.md)。

## 13. 官方资料

1. https://docs.langchain.com/oss/python/deepagents/overview
2. https://docs.langchain.com/oss/python/deepagents/customization
3. https://docs.langchain.com/oss/python/deepagents/skills
4. https://docs.langchain.com/oss/python/deepagents/profiles
5. https://docs.langchain.com/oss/python/deepagents/subagents
6. https://docs.langchain.com/oss/python/deepagents/backends
7. https://docs.langchain.com/oss/python/langchain/middleware/built-in
8. https://docs.langchain.com/oss/python/langchain/middleware/custom
9. https://docs.langchain.com/oss/python/langchain/mcp
10. https://docs.langchain.com/langsmith/evaluation-types
