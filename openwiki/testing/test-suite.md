---
type: concept
title: 测试架构与质量保证
description: 分析基于 pytest 的契约单元测试、解析器与 Toolkit 集成测试、安全防御校验（防目录穿越与严格模式）、SQLite 存储生命周期及桌面前端自动化测试。
tags: [testing, pytest, quality-assurance, regression, contracts, desktop]
sources:
  - id: openwiki-source-83ccb301171788f9ae087a4a
    resource: repo://tests/test_schema.py
  - id: openwiki-source-09d2a8f36f3ecb7ab9487ab2
    resource: repo://tests/test_store.py
generated: { by: "antigravity", at: "2026-10-19T05:32:42.641Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-19T05:51:52.758Z
---

# 测试架构与质量保证

`vertical-agent-harness` 贯彻“测试驱动与确定性验证优先”原则。系统并未将业务正确性简单托付给不可控的模型自由生成，而是围绕数据契约、安全防御边界、多租户存储隔离与引用核验算法编写了严密的确定性测试套件。

## 1. 测试套件结构

仓库中的核心单元测试集中在 [tests/](../../tests/) 目录下：

```text
tests/
├── conftest.py                # 全局 pytest fixtures 与环境清理
├── test_data_analysis.py      # 科研数据分析插件、表格画像、报告核验与 8 阶段管道测试
├── test_evaluation.py         # 评估体系、数据集与评分逻辑测试
├── test_logging.py            # 应用结构化日志、格式化脱敏、并发上下文与轮转测试
├── test_mcp.py                # MCP 服务配置、stdio/HTTP 传输、Tool Annotations 与生命周期测试
├── test_plugin_architecture.py# 后端插件目录单一 py 规范、DomainPlugin ABC 契约与分层隔离测试
├── test_schema.py             # 数据契约、解析器与 Toolkit 集成、安全防御与配置测试
├── test_stabilization.py      # 确定性重试、并发稳定性与异常恢复测试
├── test_store.py              # 原生 SQLite 存储生命周期、异步操作与复合后端隔离测试
├── test_supervisor_workflows_and_time_travel.py # Supervisor 工作流委派、三层状态契约、监督策略与 Time Travel Fork
└── test_workflow_logging.py   # 基于本地假模型的完整 Deep Agents 工作流回调执行树测试
```

运行全部测试：
```bash
pytest -q
```

## 2. 数据契约、组件集成与安全防御测试 (`test_schema.py`)

在 [tests/test_schema.py](../../tests/test_schema.py) 中，针对系统边界与子包协同定义了多组关键回归测试：

### 2.1 严格输入防御与引用核查 (`test_schema_rejects_extras` / `test_reference_mismatch`)
- **拒绝未知字段 (`test_schema_rejects_extras`)**：验证当外部向 `ReferenceCheckInput` 传入未声明字段（如 `unexpected=True`）时，Pydantic 必须抛出 `ValidationError`，防范非预期参数注入。
- **引用异常精准捕获 (`test_reference_mismatch`)**：传入正文引用 `[1]` 与 `[3]`、但参考文献列表仅有 2 篇的输入样本调用 `scan_numeric`，断言状态判定为 `CheckStatus.ISSUES`，且精确捕获 `(IssueKind.OUT_OF_RANGE, 3)` 与 `(IssueKind.UNCITED, 2)`。

### 2.2 解析器、Toolkit 与 `AgentComposer` 集成 (`test_citation_parser_and_toolkit_integration`)
- **纯函数解析与评估验证**：断言 `extract_cited_indices("Refs [1, 3-5] and [2].")` 输出 `[1, 2, 3, 4, 5]`，且 `evaluate_references` 在引用完全匹配时返回 `CheckStatus.PASSED`。
- **`BaseToolkit` 批量包装与 HITL 中断验证**：验证 `ToolContribution.from_toolkit(ResearchCitationToolkit(), effect=SideEffect.WRITE, exposure=Exposure.EAGER)` 自动将 `requires_approval` 置为 `True`，并生成 `{"allowed_decisions": ["approve", "reject"]}` 中断配置。
- **`AgentComposer` 插件注册验证**：验证 `ResearchCitationPlugin` 满足 `@runtime_checkable` 的 `DomainPlugin` 协议，且注册到 `AgentComposer` 后成功挂载工作流 `citation_audit`（`composer.workflows`）、将门控工具归入 `gated_tools`，并将技能文件挂载至 `/skills/research-citations-numeric-citation-audit/SKILL.md`。

### 2.3 防目录遍历与标识符校验 (`test_plugin_contract`)
- **恶意插件 ID 拦截**：断言包含路径遍历字符的插件标识符（如 `../../evil`）无法通过 `PluginManifest` 校验。
- **恶意技能路径拦截**：断言包含 `..` 相对跳转符的技能路径（如 `../hack/SKILL.md`）必须被 `SkillAsset` 字段校验器拦截，防止攻击者读取或覆盖宿主私有文件。

### 2.4 业务规则、模型显式配置与 CLI 验证
- **已核实结论必须有证据 (`test_verified_finding_requires_evidence`)**：验证 `AuditFinding` 当 `status="verified"` 时若证据为空，必须抛出校验异常。
- **模型显式配置与 CLI 插件列表 (`test_settings_require_explicit_model`)**：在清除环境变量 `VA_MODEL` 的情况下，实例化 `HarnessSettings` 必须因缺少必填项而报错，禁止静默回退到默认模型；显式配置后验证 `model_init_kwargs` 解包行为，并通过 `cli_main(["--list-plugins"])` 验证 CLI 正常输出 `research.citations` 插件清单。

## 3. 存储生命周期与隔离测试 (`test_store.py`)

在 [tests/test_store.py](../../tests/test_store.py) 中，对底层存储和文件系统的解耦机制进行了深入验证：

### 3.1 同步与异步 SQLite 生命周期
- **跨会话持久化 CRUD (`test_native_sqlite_store_lifecycle_and_crud`)**：
  - 会话 1：通过 `open_store` 写入用户偏好键值数据，上下文退出并关闭连接。
  - 会话 2：重新调用 `open_store` 打开数据库，验证数据可被正确重载读取，并在执行 `delete` 后返回 `None`。
  - 文件物理验证：断言工作区根目录下确实生成了 `store.sqlite` 文件。
- **异步存储验证 (`test_native_async_sqlite_store_lifecycle`)**：通过 `aopen_store` 异步上下文管理器验证对 `AsyncSqliteStore` 的 `aput` 与 `aget` 操作。

### 3.2 复合后端存储路由与字符清洗
- **工作区与记忆隔离 (`test_composite_backend_routes_memories_to_sqlite_store`)**：
  - 普通文件写入（如 `/draft.txt`）直接在本地磁盘工作区中创建物理文件。
  - 长期记忆文件（如 `/memories/AGENTS.md`）通过 `CompositeBackend` 被路由重定向至 `SqliteStore`，**磁盘上绝不会创建 `memories/` 物理文件夹**。
  - 命名空间身份哈希隔离验证：针对包含点号的租户 ID `lab.a` 与用户 ID `researcher.7`，断言其在存储中通过确定性 SHA-256 摘要派生为合法的命名空间元组，避免破坏 LangGraph 命名空间分隔。

## 4. Supervisor 工作流与 Time Travel 回溯测试 (`test_supervisor_workflows_and_time_travel.py`)

在 [tests/test_supervisor_workflows_and_time_travel.py](../../tests/test_supervisor_workflows_and_time_travel.py) 中，对 Supervisor 委派机制、三层状态隔离、监督治理策略与检查点分叉进行了全链路回归覆盖：

### 4.1 三层状态模式隔离与参数提取测试
- **三层模式强制约束 (`test_workflow_contribution_requires_explicit_schemas`)**：验证 `WorkflowContribution` 实例化时若缺少 `internal_state`、`input_schema` 或 `output_schema` 必须抛出 `ValidationError`。
- **消息提取与纯文本提取 (`test_citation_workflow_standalone_message_extraction`)**：验证工作流入口节点支持直接从 `HumanMessage` 纯文本或 Markdown ` ```json ` 代码块提取入参并执行 Pydantic 严格校验。

### 4.2 确定性监督策略治理 (`SupervisorPolicyMiddleware`)
- **白名单与租户 RBAC 拦截 (`test_supervisor_policy_blocks_unauthorized_plugin`)**：验证当主智能体试图调用未注册的工作流或无权限租户时，中间件直接拦截并返回友好错误。
- **依赖关系顺序校验 (`test_supervisor_policy_enforces_workflow_dependencies`)**：验证声明了 `depends_on` 的工作流在前置任务未成功执行前被阻断委派。
- **调度上限防御 (`test_supervisor_policy_enforces_max_dispatches`)**：验证超过 `max_dispatches` 重试阈值后立即熔断，防止主智能体与工作流陷入无限死循环。
- **结果幂等复用与结构验收 (`test_supervisor_policy_idempotent_result_reuse`)**：验证相同输入载荷的工作流调度直接复用已验收的输出，避免重复计算。

### 4.3 同步落盘与 Time Travel Fork 测试
- **跨进程会话恢复与历史追踪 (`test_sqlite_checkpointer_durability_and_history`)**：通过 `open_checkpointer` 验证 SQLite 检查点每步同步写入，并由 `get_checkpoint_history` 完整还原快照时间线。
- **状态注入与跨线程分叉 (`test_time_travel_fork_to_new_thread`)**：验证 `fork_checkpoint` 支持从指定 `checkpoint_id` 原地回放或分叉至 `--fork-thread-id`，并验证分叉后新旧线程状态彼此独立。

## 5. MCP 服务适配与 Tool Annotations 测试 (`test_mcp.py`)

在 [tests/test_mcp.py](../../tests/test_mcp.py) 中，全面覆盖了 MCP 连接配置、传输层适配、工具发现命名空间与 Tool Annotations 信任边界：

### 5.1 配置校验与传输层测试
- **Pydantic 校验与凭证保护**：测试 `MCPStdioServer`（本地子进程命令、不经过 shell、`env` 环境变量使用 `SecretStr` 脱敏）与 `MCPHttpServer`（Streamable HTTP URL、自定义请求头）。
- **stdio 与 HTTP 真实生命周期 (`test_real_stdio_and_streamable_http`)**：基于 FastMCP 启动测试服务，验证 `aopen_mcp_tools` 自动建立连接并生成带服务名前缀的工具（如 `local_echo`、`remote_echo`）。

### 5.2 Tool Annotations 顾问元数据与审批拦截
- **注解安全读取 (`test_mcp_annotations_are_advisory_metadata`)**：验证通过 `ToolContribution.mcp_annotations` 从底层 `metadata["mcp"]["tool"]["annotations"]` 安全提取 `title`、`read_only_hint` 等 snake_case 顾问提示。
- **不可信提示标注 (`untrusted hints`)**：在生成的 `to_interrupt_config()` 的 `description` 中明确包含服务端注解 JSON，并显著标注其为“不可信提示”（untrusted hints）。
- **审批安全不降级**：验证服务端提供的 `read_only_hint=True` 不会破坏宿主声明的副作用策略，凡配置为 `effect="external"` 或 `"write"` 的 MCP 工具仍必须触发 `approve`/`reject` 审批。

### 5.3 连接容错与异常清理
- **部分失败时连接全量释放 (`test_partial_discovery_failure_closes_all_connections`)**：当配置多个 MCP 服务且其中一个连接失败时，断言 `AsyncExitStack` 确保其他已打开的连接与子进程被立即清理，避免僵尸进程遗留。

## 6. 桌面前端测试套件 (`desktop/`)

参考 [desktop/TESTING.md](../../desktop/TESTING.md)，桌面前端工程包含完整的端到端验证体系：

- **TypeScript 类型核验与 Vitest 单测**：执行 `npm run typecheck` 与 `npm test` 验证渲染层状态机、协议序列化与组件逻辑。
- **Electron 真实窗口冒烟测试**：执行 `npm run build && npm run test:smoke` 启动真实 Electron 窗口，利用预加载层与模拟后端验证应用启动生命周期。
- **UI 渲染一致性校验**：通过 `node scripts/check-renderer-parity.mjs` 对比迁入组件与原版 UI 渲染树的一致性。
- **真实模型集成测试**：通过设置 `$env:DESKTOP_LIVE_TEST='1'` 执行 `npx vitest run tests/live-runtime.test.ts`，验证 Python Bridge 与实际大模型调用链路。

## 相关页面

- [插件机制与贡献契约](../architecture/plugin-system.md)：了解插件在白名单与清单校验上的契约约束。
- [状态隔离与分层存储](../architecture/state-and-storage.md)：了解 `SqliteStore`、`SqliteSaver` 与 Time Travel Fork。
- [数字引用审计端到端流程](../workflows/citation-audit.md)：查看被测端到端流程。
- [MedPeer 桌面前端工作台](../integrations/desktop-workbench.md)：了解桌面工作台运行时的架构与测试细节。
