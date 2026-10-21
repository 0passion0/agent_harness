---
type: concept
title: Harness 系统架构与核心组装
description: 阐述基于 deepagents.create_deep_agent 的单一智能体架构、AgentComposer 组装流程、子模块领域划分与九大硬约束。
tags: [architecture, agent-harness, deepagents, composition, security]
sources:
  - id: openwiki-source-3990cd94cab2bb96546d516e
    resource: repo://packages/agent-harness/src/vertical_harness/__init__.py
  - id: openwiki-source-922c9b71ee11988b0f023000
    resource: repo://packages/agent-harness/src/vertical_harness/core/compose.py
verified:
  - by: openwiki/0.7.2
    at: 2026-10-21T02:48:18.086Z
generated: { by: "antigravity", at: "2026-10-21T02:48:18.086Z" }
---

# Harness 系统架构与核心组装

`vertical-agent-harness` 是以 `deepagents.create_deep_agent` 为基础构建的高性能单一智能体脚手架（Agent Harness）。系统遵循“不重复造轮子”与“极简主义（Ponytail）”原则，将底层 Agent 循环、原生子任务委派、摘要压缩与工具调用修复委托给底层框架，宿主层专注于模型路由、桌面协议适配、受控脚本执行、文档格式解析及安全中间件治理。

## 1. 架构职责与子包领域划分

根据 [packages/agent-harness/src/vertical_harness/__init__.py](../../packages/agent-harness/src/vertical_harness/__init__.py) 与 [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)，宿主包按清晰的单一职责划分为九大业务领域：

- **`core/` 核心编排与路由**（[compose.py](../../packages/agent-harness/src/vertical_harness/core/compose.py)、[routing.py](../../packages/agent-harness/src/vertical_harness/core/routing.py)、[context.py](../../packages/agent-harness/src/vertical_harness/core/context.py)）：`AgentComposer` 统一装配主智能体执行图；`ModelRouting` 管理模型 Provider 实例化；`RunContext` 承载不可变运行上下文。
- **`bridge/` 桌面交互桥接**（[attachments.py](../../packages/agent-harness/src/vertical_harness/bridge/attachments.py)、[messages.py](../../packages/agent-harness/src/vertical_harness/bridge/messages.py)、[runtime.py](../../packages/agent-harness/src/vertical_harness/bridge/runtime.py)）：提供 JSON-RPC 消息封装、多模态附件解析及桌面端运行环境初始化。
- **`config/` 类型化配置模型**（[settings.py](../../packages/agent-harness/src/vertical_harness/config/settings.py)、[checkpoint_config.py](../../packages/agent-harness/src/vertical_harness/config/checkpoint_config.py)）：读取 `VA_*` 环境变量，管理全局运行时参数、检查点配置与任务分级。
- **`langchain/` 流式与回调机制**（[streaming.py](../../packages/agent-harness/src/vertical_harness/langchain/streaming.py)、[stream_event.py](../../packages/agent-harness/src/vertical_harness/langchain/stream_event.py)、[callbacks.py](../../packages/agent-harness/src/vertical_harness/langchain/callbacks.py)）：标准化转换底层图的流式事件（`StreamEvent`），分发进度并处理人工审批挂起中断。
- **`log/` 安全可观测性**（[logging.py](../../packages/agent-harness/src/vertical_harness/log/logging.py)、[observability.py](../../packages/agent-harness/src/vertical_harness/log/observability.py)）：结构化日志输出与 LangSmith 上下文追踪，严格执行敏感脱敏，禁止记录用户原文与密钥。
- **`middleware/` 运行时拦截中间件**（[approval.py](../../packages/agent-harness/src/vertical_harness/middleware/approval.py)、[context_limit.py](../../packages/agent-harness/src/vertical_harness/middleware/context_limit.py)、[permission.py](../../packages/agent-harness/src/vertical_harness/middleware/permission.py)、[routing.py](../../packages/agent-harness/src/vertical_harness/middleware/routing.py)）：负责审批中断拦截、Token 预算熔断、工作区越界防御及模型动态分流。
- **`storage/` 存储与虚拟文档**（[checkpoint.py](../../packages/agent-harness/src/vertical_harness/storage/checkpoint.py)、[document_backend.py](../../packages/agent-harness/src/vertical_harness/storage/document_backend.py)、[store.py](../../packages/agent-harness/src/vertical_harness/storage/store.py)）：管理状态持久化生命周期，基于 MarkItDown 实现文档读取与文件操作协议。
- **`tools/` 受控外部工具集**（[python_execution.py](../../packages/agent-harness/src/vertical_harness/tools/python_execution.py)、[search.py](../../packages/agent-harness/src/vertical_harness/tools/search.py)、[tool_details.py](../../packages/agent-harness/src/vertical_harness/tools/tool_details.py)、[user_interaction.py](../../packages/agent-harness/src/vertical_harness/tools/user_interaction.py)）：提供先保存后执行的 Python 脚本运行、网络检索及人机交互。
- **`utils/` 辅助工具函数**（[structured_formatter.py](../../packages/agent-harness/src/vertical_harness/utils/structured_formatter.py)、[usage.py](../../packages/agent-harness/src/vertical_harness/utils/usage.py)）：格式化输出辅助与 Token 成本估算。

在系统架构边界上：
- **不引入业务插件、MCP、技能注册、长期记忆或重型自研工作流**。
- **主智能体与原生委派**：主循环使用 `create_deep_agent`，子任务通过框架原生 `task` 委派，强制继承父任务的工作区、敏感路径拦截、审批与预算限制。
- **纯类接口**：所有业务能力直接通过类方法调用，`__all__` 仅在 `__init__.py` 中显式维护，杜绝模块级别名与已废弃兼容层。

## 2. Agent 组装流程 (`AgentComposer.build_agent`)

所有核心运行时组件在 [packages/agent-harness/src/vertical_harness/core/compose.py](../../packages/agent-harness/src/vertical_harness/core/compose.py) 中通过 `AgentComposer.build_agent` 完成编排：

```mermaid
graph TD
    A["HarnessSettings 配置与工作区解析"] --> B["实例化 DocumentFilesystemBackend (含 MarkItDown)"]
    B --> C["配置敏感文件沙箱规则 FilesystemPermission (拒绝 .env 与数据库文件)"]
    C --> D["构建中间件栈: TodoList / ModelRouter / Summarization / ContextLimit / Permission / Approval"]
    D --> E["装配工具集: WebSearch / PythonExecution / UserInteraction"]
    E --> F["配置原生子智能体 subagents (general-purpose 继承权限与中间件)"]
    F --> G["调用 deepagents.create_deep_agent 编译状态图"]
```

组装的核心步骤包括：
1. **工作区目录解析与挂载**：确保 `settings.resolved_workspace_root` 物理目录存在，挂载虚拟文件系统 `DocumentFilesystemBackend(root_dir=root, virtual_mode=True)`。
2. **敏感路径沙箱策略配置**：
   - 严格拒绝读取和写入 `/**/.env` 与 `/**/.env.*`。
   - 动态识别检查点数据库（`resolved_checkpoint_target`）、存储数据库（`resolved_store_target`）及日志文件（`log_file`），生成模式规则（如 `-wal`、`-shm`、`-journal`）强制拒绝大模型越权操作。
3. **中间件链条装配**：
   - 可选 `TodoListMiddleware()` 用于长任务规划。
   - 动态模型路由 `ModelRouterMiddleware`。
   - `SummarizationMiddleware`：在达到有效上下文容量 90% 时触发压缩摘要。
   - `ContextLimitMiddleware`：全面请求预算与 Token 熔断拦截。
   - `DesktopPermissionMiddleware` 与 `DesktopApprovalMiddleware`：权限越界防御与人机协同审批拦截。
   - `FilesystemMiddleware`：注册 `ls`、`read_file`、`write_file`、`edit_file`、`delete`、`glob`、`grep` 文件工具。
4. **受控外部工具集挂载**：
   - `WebSearch.web_search`：事实核验网络检索。
   - `PythonExecution.python_file_tool`：以宿主当前权限执行工作区内预先保存的独立 `.py` 脚本文件。
   - `UserInteraction.ask_user`：缺失必要信息时的人机确认。
5. **原生子智能体委派 (`subagents`)**：
   - 配置 `general-purpose` 子智能体，共享工作区、权限策略与中间件约束，防止委派越权。

## 3. 基础系统提示词 (`BASE_PROMPT`)

在 `core/compose.py` 中定义了全局 `BASE_PROMPT`，确立系统安全与验证准则：
- 检索与文件工具输出视为非受信数据（untrusted data）。
- 文档与代码优先采用 `grep`、`glob` 与按行读取；局部修改使用 `edit_file`，禁止整文件重写。
- Office 与 PDF 优先通过 MarkItDown 读取 Markdown；复杂表格与生成任务交由保存的 Python 脚本处理。
- 脚本执行以宿主身份运行，必须验证退出码与输出，严禁臆造成功结果。

## 相关页面

- [快速入门指南](../quickstart.md)：了解安装与 CLI 交互入口。
- [动态模型路由与模型适配](./model-routing.md)：查看基于 task_tier 的动态模型分流。
- [状态管理、检查点与文档后端](./state-and-storage.md)：了解 CheckpointFactory 与 DocumentFilesystemBackend。
- [桌面端集成与 JSON-RPC 通信](../integrations/desktop-workbench.md)：了解桌面端 AgentLoop 生命周期管理。
