---
type: concept
title: 插件机制与贡献契约
description: 深入解析基于 Python entry points 的无侵入插件发现机制、白名单安全校验、Pydantic 贡献契约及技能门控。
tags: [plugin-system, entry-points, contracts, pydantic, hitl]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T03:23:43.651Z
sources:
  - id: openwiki-source-c81ed3d87dea8f94def34288
    resource: repo://packages/agent-harness/src/vertical_harness/compose.py
  - id: openwiki-source-3bd2695187be9dfdc5075da7
    resource: repo://packages/agent-harness/src/vertical_harness/contracts.py
  - id: openwiki-source-47c55717474cd06d6a572d94
    resource: repo://packages/agent-harness/src/vertical_harness/discovery.py
generated: { by: "antigravity", at: "2026-10-18T03:23:43.651Z" }
---

# 插件机制与贡献契约

`vertical-agent-harness` 采用插件化架构，使得第三方领域包（如学术引用审计、金融核查、合规审查等）可以在完全不侵入、不修改主工程代码的前提下，向 Agent 注入专属的工具、技能、子智能体与系统指令。

## 1. 入口点与动态发现机制

插件通过标准 Python distribution 的 `entry points` 进行声明与分发。

### 1.1 插件端声明 (`pyproject.toml`)

每个插件作为一个独立的 Python 包，在其 `pyproject.toml` 中向 `vertical_agent.plugins` 入口点组注册实现类：

```toml
[project.entry-points."vertical_agent.plugins"]
"research.citations" = "research_citation.plugin:ResearchCitationPlugin"
```

### 1.2 宿主端白名单发现 (`discovery.py`)

在 [packages/agent-harness/src/vertical_harness/discovery.py](../../packages/agent-harness/src/vertical_harness/discovery.py) 中，`discover_plugins` 函数负责安全加载插件：

- **白名单严格过滤**：只扫描并加载 `settings.enabled_plugins` 白名单中明确列出的插件。白名单中若有重复 ID 直接抛出 `ValueError`。
- **缺失与冲突检查**：若白名单中要求的插件未安装，或者发现重复的同名入口点，立即抛出异常阻断启动（Fail-Closed 原则）。
- **清单与入口点一致性校验**：动态加载入口点工厂后，通过 `PluginManifest.model_validate` 验证插件元数据，并强制要求 `manifest.plugin_id` 必须与入口点注册名称完全一致。
- **协议实现校验**：确保插件实例实现了可调用的 `build()` 方法，否则抛出 `TypeError`。

```python
candidates = entry_points(group=PLUGIN_GROUP)
# 严格按照白名单顺序加载，并执行 manifest.plugin_id == identifier 的一致性核对
```

## 2. 强类型贡献契约 (`contracts.py`)

在 [packages/agent-harness/src/vertical_harness/contracts.py](../../packages/agent-harness/src/vertical_harness/contracts.py) 中，系统基于 Pydantic v2 定义了强类型契约模型，所有模型均继承自开启 `extra="forbid"` 的 `StrictModel`，杜绝未声明的隐式参数：

### 2.1 核心数据结构

| 契约类 | 核心属性与约束 | 设计目的 |
|---|---|---|
| `PluginManifest` | `plugin_id` (正则限制小写字母、数字、点/减号/下划线), `api_version=1`, `version` (SemVer 格式), `description` (>=8 字符) | 插件元数据与接口版本自洽声明 |
| `SkillAsset` | `relative_path` (严格禁止绝对路径、禁止 `.` 或 `..` 目录穿越、必须在子目录下), `content` | 随包静态打包的 Markdown 技能文件 |
| `ToolContribution` | `tool: BaseTool`, `effect: SideEffect` (READ / WRITE / EXTERNAL), `exposure: Exposure` (EAGER / SKILL) | 声明工具副作用与大模型可见时机 |
| `PluginContribution` | `tools: list[ToolContribution]`, `skills: list[SkillAsset]`, `middleware: list[AgentMiddleware]`, `subagents`, `instructions` | 插件 `build()` 方法的标准返回载荷 |

### 2.2 防目录遍历与安全路径校验

`SkillAsset` 实现了针对 `relative_path` 的自定义字段校验器：
- 严禁任何绝对路径；
- 严禁包含 `.` 或 `..` 分段，防止利用恶意相对路径读取或覆盖宿主系统敏感文件；
- 限制路径深度至少为两级（如 `numeric-citation-audit/SKILL.md`），并校验路径字符合法性。

## 3. 工具暴露分级与人工审批 (HITL)

系统区分了两种维度的工具控制：

### 3.1 渐进式技能门控 (Exposure.SKILL)
- **直接暴露 (`Exposure.EAGER`)**：在 Agent 初始化时直接向模型暴露，模型随时可调用。
- **技能门控 (`Exposure.SKILL`)**：当宿主开启 `enable_skill_gating=True` 时，该类工具初始状态对大模型隐藏。仅当模型通过 `SkillsMiddleware` 激活对应技能的 `SKILL.md`（通过 `metadata.include_tools` 声明）后，工具才动态加载至上下文，大幅减少长工具列表带来的上下文浪费与幻觉。

### 3.2 副作用拦截与人机协同 (HITL)
- 任何声明为 `SideEffect.WRITE`（写操作）或 `SideEffect.EXTERNAL`（外部系统网络调用）的工具，在 `build_agent` 组装时会触发强制校验：
  - **禁止门控**：产生副作用的工具必须设置为 `Exposure.EAGER`，确保审批规则始终全局可见；若误设为 `Exposure.SKILL` 则抛出 `ValueError`。
  - **审批拦截**：系统自动在 LangGraph 编译配置中注册 `interrupt_on[tool_name] = {"allowed_decisions": ["approve", "reject"]}`。当大模型试图调用该工具时，执行流立即挂起并向外部返回 `__interrupt__`，必须由人工或审计接口通过 `Command(resume=...)` 显式确认后方可继续执行。

## 相关页面

- [Harness 系统架构与核心组装](./overview.md)：了解插件在 `build_agent` 中的组装逻辑。
- [学术引用审计插件实现](../integrations/citation-plugin.md)：查看 `research.citations` 插件的实现范例。
