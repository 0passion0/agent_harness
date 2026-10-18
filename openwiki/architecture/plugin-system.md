---
type: concept
title: 插件机制与贡献契约
description: 深入解析基于 Python entry points 的无侵入插件发现机制、白名单安全校验、Pydantic 贡献契约、BaseToolkit 适配及技能门控与 HITL。
tags: [plugin-system, entry-points, contracts, pydantic, hitl]
sources:
  - id: openwiki-source-2540ded2e9118384bbc3e1a9
    resource: repo://packages/agent-harness/src/vertical_harness/engine/compose.py
  - id: openwiki-source-eda43ae96b105fae785c7609
    resource: repo://packages/agent-harness/src/vertical_harness/plugins/contracts.py
  - id: openwiki-source-22fd106252c69a22894fb255
    resource: repo://packages/agent-harness/src/vertical_harness/plugins/discovery.py
generated: { by: "antigravity", at: "2026-10-18T06:56:09.371Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T06:56:09.371Z
---

# 插件机制与贡献契约

`vertical-agent-harness` 采用插件化架构，使得第三方领域包（如学术引用审计、金融核查、合规审查等）可以在完全不侵入、不修改主工程代码的前提下，向 Agent 注入专属的工具、技能、子智能体与系统指令。相关实现集中在 `vertical_harness.plugins` 子包中，并通过 `vertical_harness` 顶层统一导出。

## 1. 入口点与动态发现机制

插件通过标准 Python distribution 的 `entry points` 进行声明与分发。

### 1.1 插件端声明 (`pyproject.toml`)

每个插件作为一个独立的 Python 包，在其 `pyproject.toml` 中向 `vertical_agent.plugins` 入口点组注册实现类：

```toml
[project.entry-points."vertical_agent.plugins"]
"research.citations" = "research_citation.plugin:ResearchCitationPlugin"
```

### 1.2 宿主端白名单发现 (`plugins/discovery.py`)

在 [packages/agent-harness/src/vertical_harness/plugins/discovery.py](../../packages/agent-harness/src/vertical_harness/plugins/discovery.py) 中，`discover_plugins` 结合辅助函数 `_index_matching_entry_points` 与 `_instantiate_and_verify` 负责安全加载插件：

- **白名单严格过滤**：使用 `Counter` 检查 `allowlist` 中是否存在重复 ID，随后只扫描并索引 `settings.enabled_plugins` 白名单中明确列出的插件。
- **缺失与冲突检查**：若在 `vertical_agent.plugins` 入口点组中发现重复的同名入口点，或白名单中要求的插件未安装，立即抛出 `ValueError` 阻断启动（Fail-Closed 原则）。
- **清单与入口点一致性校验 (`_instantiate_and_verify`)**：动态加载入口点工厂并实例化后，通过 `PluginManifest.model_validate` 验证插件元数据，并强制要求 `manifest.plugin_id` 必须与入口点注册名称 `identifier` 完全一致。
- **协议实现校验**：确保插件实例符合 `@runtime_checkable` 的 `DomainPlugin` 协议且实现了可调用的 `build()` 方法，否则抛出 `TypeError`。

```python
matches = _index_matching_entry_points(allowlist_set)
# 严格按照白名单顺序加载，并执行 manifest.plugin_id == identifier 的一致性核对
return [_instantiate_and_verify(identifier, matches[identifier]) for identifier in allowlist]
```

## 2. 强类型贡献契约 (`plugins/contracts.py`)

在 [packages/agent-harness/src/vertical_harness/plugins/contracts.py](../../packages/agent-harness/src/vertical_harness/plugins/contracts.py) 中，系统基于 Pydantic v2 定义了强类型契约模型，所有模型均继承自开启 `extra="forbid"` 的 `StrictModel`，杜绝未声明的隐式参数：

### 2.1 核心数据结构

| 契约类 | 核心属性与辅助方法 | 设计目的 |
|---|---|---|
| `PluginManifest` | `plugin_id` (`PluginIdStr`), `api_version=1`, `version` (`SemVerStr`), `description` (>=8 字符), `normalized_prefix` | 插件元数据与接口版本自洽声明 |
| `SkillAsset` | `relative_path`, `content`, `to_virtual_path()`, `to_upload_payload()` | 随包静态打包的 Markdown 技能文件与虚拟路径映射 |
| `ToolContribution` | `tool: BaseTool`, `effect: SideEffect` (`READ` / `WRITE` / `EXTERNAL`), `exposure: Exposure` (`EAGER` / `SKILL`), `from_toolkit()` | 声明工具副作用、可见时机及 `BaseToolkit` 批量导出 |
| `PluginContribution` | `tools`, `skills`, `middleware`, `subagents`, `instructions`, `format_system_instruction()` | 插件 `build()` 方法的标准返回载荷 |

### 2.2 防目录遍历、安全路径校验与虚拟路径隔离

`SkillAsset` 实现了针对 `relative_path` 的自定义字段校验器与路径转换方法：
- **严格校验 (`validate_relative_path`)**：严禁反斜杠 `\`、严禁任何绝对路径、严禁包含 `.` 或 `..` 分段（防止利用恶意相对路径读取或覆盖宿主系统敏感文件），并强制要求路径深度至少为两级（如 `numeric-citation-audit/SKILL.md`）且各分段符合 `_SAFE_SKILL_SEGMENT` 正则。
- **命名空间隔离 (`to_virtual_path` / `to_upload_payload`)**：自动将插件 ID 中的 `.` 替换为 `-`，拼接生成虚拟路径 `/skills/{plugin-id}-{skill-dir}/...` 及 UTF-8 编码字节流，直接供 Deep Agents Backend `upload_files` 使用。

### 2.3 LangChain `BaseToolkit` 批量适配 (`ToolContribution.from_toolkit`)

为方便集成遵循 LangChain 标准 `BaseToolkit` 规范的工具包，`ToolContribution.from_toolkit(toolkit, effect=..., exposure=...)` 类方法支持一键提取 `toolkit.get_tools()` 中的全部工具并批量封装为具有统一副作用与暴露策略的 `ToolContribution` 列表。

## 3. 工具暴露分级与人工审批 (HITL)

系统在 [engine/compose.py](../../packages/agent-harness/src/vertical_harness/engine/compose.py) 的 `AgentComposer._register_tool` 中区分了两种维度的工具控制：

### 3.1 渐进式技能门控 (`Exposure.SKILL`)
- **直接暴露 (`Exposure.EAGER`)**：在 Agent 初始化时直接放入 `eager_tools` 向模型暴露，模型随时可调用。
- **技能门控 (`Exposure.SKILL`)**：当宿主开启 `enable_skill_gating=True` 时，该类工具归入 `gated_tools`，初始状态对大模型隐藏。仅当模型通过 `SkillsMiddleware` 激活对应技能的 `SKILL.md`（通过 `metadata.include_tools` 声明）后，工具才动态加载至上下文，大幅减少长工具列表带来的上下文浪费与幻觉。

### 3.2 副作用拦截与人机协同 (HITL)
- 任何声明为 `SideEffect.WRITE`（写操作）或 `SideEffect.EXTERNAL`（外部系统网络调用）的工具（`item.requires_approval == True`），在 `AgentComposer._register_tool` 注册时会触发强制校验：
  - **禁止门控**：产生副作用的工具必须设置为 `Exposure.EAGER`，确保审批规则始终全局可见；若误设为 `Exposure.SKILL` 则直接抛出 `ValueError("Side-effect tools must be eager to enforce approvals")`。
  - **审批拦截**：通过 `item.to_interrupt_config()` 自动在 `interrupts[name]` 中注册 `InterruptOnConfig(allowed_decisions=["approve", "reject"])`。当大模型试图调用该工具时，执行流立即挂起并向外部返回 `__interrupt__`，必须由人工或审计接口通过 `Command(resume=...)` 显式确认后方可继续执行。

## 相关页面

- [Harness 系统架构与核心组装](./overview.md)：了解插件在 `AgentComposer` 与 `build_agent` 中的组装逻辑。
- [学术引用审计插件实现](../integrations/citation-plugin.md)：查看 `research.citations` 插件与 `ResearchCitationToolkit` 的实现范例。
