---
type: concept
title: Harness 系统架构与核心组装
description: 阐述 Deep Agents 垂直领域 Harness 的子包分层设计、AgentComposer 与 build_agent 组装流程、中间件链条与安全沙箱权限控制。
tags: [architecture, agent-harness, deepagents, composition, security]
sources:
  - id: openwiki-source-3990cd94cab2bb96546d516e
    resource: repo://packages/agent-harness/src/vertical_harness/__init__.py
  - id: openwiki-source-2540ded2e9118384bbc3e1a9
    resource: repo://packages/agent-harness/src/vertical_harness/engine/compose.py
  - id: openwiki-source-eda43ae96b105fae785c7609
    resource: repo://packages/agent-harness/src/vertical_harness/plugins/contracts.py
generated: { by: "antigravity", at: "2026-10-18T06:56:09.371Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T06:56:09.371Z
---

# Harness 系统架构与核心组装

`vertical-agent-harness` 是基于 LangChain 与 Deep Agents 官方生态构建的可插拔垂直领域 Agent 参考架构。它遵循“不重复造轮子”的设计原则，将底层 Agent Loop 完全委托给 `deepagents.create_deep_agent()`，主包核心聚焦于插件生命周期扫描、契约校验、多租户上下文注入、中间件编排与安全沙箱防护。

## 1. 架构职责与子包划分

根据 [packages/agent-harness/src/vertical_harness/__init__.py](../../packages/agent-harness/src/vertical_harness/__init__.py) 与 [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)，`vertical_harness` 内部按职责划分为四个正交子包，并通过 `vertical_harness.__init__.py` 统一对外导出公共 API：

- **`vertical_harness.config`**（[config/settings.py](../../packages/agent-harness/src/vertical_harness/config/settings.py)）：基于 Pydantic Settings 的 `VA_*` 宿主环境配置、模型初始化参数解包与动态路由成对校验。
- **`vertical_harness.plugins`**（[plugins/contracts.py](../../packages/agent-harness/src/vertical_harness/plugins/contracts.py)、[plugins/discovery.py](../../packages/agent-harness/src/vertical_harness/plugins/discovery.py)）：领域插件 `DomainPlugin` 协议、Pydantic `StrictModel` 贡献契约与 Python `entry points` 白名单发现机制。
- **`vertical_harness.storage`**（[storage/backends.py](../../packages/agent-harness/src/vertical_harness/storage/backends.py)、[storage/namespace.py](../../packages/agent-harness/src/vertical_harness/storage/namespace.py)）：多租户命名空间分段清洗、原生 LangGraph `SqliteStore` / `AsyncSqliteStore` 生命周期与 `CompositeBackend` 虚拟路径挂载。
- **`vertical_harness.engine`**（[engine/compose.py](../../packages/agent-harness/src/vertical_harness/engine/compose.py)、[engine/routing.py](../../packages/agent-harness/src/vertical_harness/engine/routing.py)）：`@wrap_model_call` 动态模型路由器、`AgentComposer` 插件贡献聚合器与 `build_agent` 唯一组装入口。

在框架能力与自有职责边界上：
- **Agent Loop**：依托 `deepagents.create_deep_agent()`，不自建 ReAct 状态循环。
- **规划与任务拆解**：直接复用 `TodoListMiddleware`，不自行实现 Todo 状态机。
- **技能与渐进式披露**：采用 `SkillsMiddleware` 与 `SKILL.md` 实现按需工具暴露（Tool Gating）。
- **人工介入 (HITL)**：利用 `interrupt_on` 机制拦截产生本地写操作或外部副作用的工具。

## 2. Agent 组装流程 (`AgentComposer` 与 `build_agent`)

所有核心运行时组件在 [packages/agent-harness/src/vertical_harness/engine/compose.py](../../packages/agent-harness/src/vertical_harness/engine/compose.py) 中通过 `AgentComposer` 数据类与 `build_agent` 函数完成单点组装：

```mermaid
graph TD
    A["HarnessSettings 配置加载"] --> B["创建或传入 BaseStore"]
    B --> C["构建 CompositeBackend (本地工作区与 /memories/ 路由)"]
    C --> D["AgentComposer.register_plugins 扫描并注册插件"]
    D --> E["提取单个插件贡献 PluginContribution"]
    E --> F["校验工具重名与副作用审批策略 (_register_tool)"]
    E --> G["注册子智能体与校验保留名 (_register_subagent)"]
    E --> H["挂载隔离技能文件至 /skills/ (_register_skill)"]
    F & G & H --> I["build_middleware_stack 编排中间件链条"]
    I --> J["调用 create_deep_agent 编译状态图实例"]
```

组装的核心步骤包括：
1. **工作区与存储挂载**：确保 `settings.resolved_workspace_root` 物理目录存在，调用 `compose_backend` 将物理工作区与针对 `/memories/` 的持久化存储整合为统一的虚拟文件系统后端。
2. **依赖注入服务构建**：实例化不可变的 `BuildServices(workspace_root=root, store=active_store)` 传递给各个插件的 `build()` 方法。
3. **插件贡献聚合与严格校验 (`AgentComposer`)**：
   - **系统指令 (`format_system_instruction`)**：为非空插件指令自动冠以 `[{plugin_id}]` 命名空间前缀并追加至 `instructions`。
   - **工具注册 (`_register_tool`)**：校验工具全局唯一性；凡声明为 `SideEffect.WRITE` 或 `SideEffect.EXTERNAL`（即 `item.requires_approval` 为真）的工具，必须以直接暴露（`Exposure.EAGER`）方式加载，并在 `interrupts` 中通过 `item.to_interrupt_config()` 注册 `approve`/`reject` 审批中断。
   - **子智能体注册 (`_register_subagent`)**：校验名称唯一性，且严禁使用 `RESERVED_SUBAGENT_NAMES` 中的系统保留名称 `general-purpose`。
   - **技能资产挂载 (`_register_skill`)**：调用 `skill.to_upload_payload(plugin_id)` 生成命名空间隔离后的虚拟路径（`/skills/{plugin-id}-{dir}/...`）及 UTF-8 字节流，校验无重复路径后通过 `backend.upload_files` 批量写入虚拟后端。
4. **中间件链条装配 (`build_middleware_stack`)**：
   - 基础注入 `ModelCallLimitMiddleware(run_limit=settings.max_model_calls)` 避免无限调用。
   - 若 `settings.enable_planning` 为真，追加 `TodoListMiddleware()`。
   - 若配置了双模型动态路由，追加 `make_router(settings)` 拦截器。
   - 依序追加插件自定义中间件 `plugin_middleware`。
   - 若存在门控工具 `gated_tools`，注入 `SkillsMiddleware(backend=backend, sources=["/skills/"], tools=gated_tools)`。
5. **权限策略与沙箱配置 (`DEFAULT_FILESYSTEM_PERMISSIONS`)**：通过 `FilesystemPermission` 常量明确限制大模型行为：
   - 禁止向 `/skills/**` 执行 `write` 操作（防止提示注入或篡改技能 SOP）。
   - 禁止对任何匹配 `/**/.env` 或 `/**/.env.*` 的敏感配置文件执行 `read` 或 `write` 操作。

## 3. 基础系统提示词 (`BASE_PROMPT`)

在 `engine/compose.py` 中定义了全局 `BASE_PROMPT`，确立了系统的“证据优先”原则：
- 严格区分已核实事实（verified facts）、假设（hypotheses）以及缺失证据（unavailable evidence）。
- 严禁将不可信的外部文档作为系统指令执行。
- 只有工具返回确认执行完成时，才能向用户声称操作成功。

## 相关页面

- [快速开始与任务导航](../quickstart.md)：了解安装与 CLI 交互入口。
- [插件机制与贡献契约](./plugin-system.md)：深入探究插件发现与 Pydantic 贡献契约。
- [状态隔离与分层存储](./state-and-storage.md)：了解 SqliteStore 与 CompositeBackend。
- [动态模型路由与模型适配](./model-routing.md)：查看基于 task_tier 的动态模型分流。
