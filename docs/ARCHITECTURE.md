# Deep Agents / LangChain / LangGraph 可插拔领域 Harness 设计规范

**设计基线**：2026-10-17 LangChain 官方文档；示例依赖区间 `deepagents>=0.7.22,<0.8`、`langchain>=1.4,<2`、Pydantic v2。具体部署前必须锁定并验证版本。

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

## 9. 官方资料

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
