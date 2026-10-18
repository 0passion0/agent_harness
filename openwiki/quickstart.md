---
type: concept
title: 快速开始与任务导航
description: Deep Agents 垂直领域插件化 Harness 快速启动指南、环境安装、CLI 使用方法及全库任务路由导航。
tags: [quickstart, navigation, installation, cli, overview]
sources:
  - id: openwiki-source-68add2df392f9f491f54a7b3
    resource: repo://packages/agent-harness/pyproject.toml
  - id: openwiki-source-3738efb5862ea362ae17bac1
    resource: repo://packages/agent-harness/src/vertical_harness/cli.py
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
generated: { by: "antigravity", at: "2026-10-18T04:37:34.297Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T06:56:09.371Z
---

# 快速开始与任务导航

欢迎阅读 `vertical-agent-harness` 知识库。本仓库基于 LangChain 与 Deep Agents 构建了一套面向垂直领域的插件化 Agent 参考架构，采用双独立 Python distribution：通用宿主底座 `vertical-agent-harness` 与演示领域插件 `vertical-plugin-research-citation`。

---

## 1. 5 分钟快速上手

### 1.1 环境初始化与安装

推荐使用 Python 3.11+ 虚拟环境或 uv workspace：

```bash
# 1. 创建并激活虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 2. 以可编辑模式同时安装宿主与插件两个独立分发包
python -m pip install -e ./packages/agent-harness -e ./packages/research-citation

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env，确保配置 VA_MODEL (如 deepseek:deepseek-flash) 与 VA_API_KEY
```

### 1.2 运行验证与初次执行

```bash
# 查看当前已加载的插件清单
vertical-agent --list-plugins

# 执行一次学术手稿引用核查
vertical-agent "检查下面正文的参考文献索引：正文 [1] 和 [3]；参考文献依次为 A、B。"

# 运行全套单元测试
pytest -q
```

---

## 2. 命令行接口 (CLI) 选项

命令行入口 `vertical-agent`（在 [pyproject.toml](../packages/agent-harness/pyproject.toml) 中声明指向 `vertical_harness.cli:main`）由 [packages/agent-harness/src/vertical_harness/cli.py](../packages/agent-harness/src/vertical_harness/cli.py) 中的 `_build_parser`、`_print_plugins` 与 `_invoke_prompt` 实现，支持以下核心运行参数：

| 参数 | 说明 | 示例 |
|---|---|---|
| `prompt` | 传递给 Agent 的自然语言任务提示词 | `"请核对手稿引文..."` |
| `--list-plugins` | 打印当前生效的插件白名单元数据 JSON 并退出 | `vertical-agent --list-plugins` |
| `--thread-id` | 指定 LangGraph 持久化会话线程 ID（默认生成随机 UUID） | `--thread-id task-1001` |
| `--tenant-id` | 指定多租户隔离标识符（默认 `demo`） | `--tenant-id lab_alpha` |
| `--user-id` | 指定用户隔离标识符（默认 `demo`） | `--user-id researcher_7` |
| `--task-tier` | 任务层级选择：`quality`（高品质型，默认 `TaskTier.QUALITY`）或 `economy`（经济型） | `--task-tier economy` |

---

## 3. 任务路由与文档地图 (Task-Routing Map)

为了帮助开发者和编码智能体快速定位代码与系统设计，全库 Wiki 按照核心系统域组织为以下导航结构：

### 🏗️ 系统架构域 (Architecture)
- [系统架构与核心组装](architecture/overview.md)：理解四大子包分层、`AgentComposer` 与 `build_agent` 核心组装流程、中间件装配、权限沙箱与证据优先系统提示词。
- [插件机制与贡献契约](architecture/plugin-system.md)：掌握基于 Python `entry points` 的动态发现、白名单隔离、Pydantic `PluginContribution` 契约、`BaseToolkit` 批量包装及人工审批 (HITL)。
- [状态隔离与分层存储](architecture/state-and-storage.md)：了解会话 Checkpointer、多租户命名空间字符清洗、`SqliteStore` 生命周期及 `CompositeBackend` 虚拟路径路由。
- [动态模型路由与模型适配](architecture/model-routing.md)：探究基于 `wrap_model_call` 与 `RunContext.task_tier` 的经济/品质模型分流机制。

### 🔌 领域插件与集成 (Integrations)
- [学术引用审计插件实现](integrations/citation-plugin.md)：剖析 `vertical-plugin-research-citation` 的 `checker`/`models` 子包结构、`extract_cited_indices` 与 `evaluate_references` 算法、`ResearchCitationToolkit` 及强约束 `AuditFinding`。

### 🔄 端到端工作流 (Workflows)
- [数字引用审计端到端流程](workflows/citation-audit.md)：跟踪从 CLI 发起任务、`SkillsMiddleware` 感知 `SKILL.md`、门控工具按需暴露到返回结构化报告的完整时序。

### ⚙️ 运维与配置 (Operations)
- [环境变量配置与运维部署](operations/configuration.md)：查阅 `VA_*` 环境变量规范、`SecretStr` 密钥保护机制与 SQLite 存储目标路径推断规则。

### 🧪 测试与质量保证 (Testing)
- [测试架构与质量保证](testing/test-suite.md)：了解 pytest 测试套件、解析器与 Toolkit 集成验证、防目录遍历攻击断言、严格输入防护与存储持久化回归测试。
