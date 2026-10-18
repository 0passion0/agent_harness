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
generated: { by: "antigravity", at: "2026-10-18T11:09:29.262Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T12:40:07.975Z
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

# 查看当前已注册的 LangGraph 确定性工作流清单（含三层状态契约与监督策略）
vertical-agent --list-workflows

# 执行一次学术手稿引用核查
vertical-agent --thread-id demo-thread "检查下面正文的参考文献索引：正文 [1] 和 [3]；参考文献依次为 A、B。"

# 查看指定会话的检查点历史（Time Travel 快照链）
vertical-agent --thread-id demo-thread --history

# 从历史检查点分叉到新线程重新核查（Time Travel Fork）
vertical-agent --thread-id demo-thread \
  --checkpoint-id <checkpoint_id> \
  --fork-thread-id demo-thread-fork \
  --update-state '{"messages": [{"role": "user", "content": "使用修正后的参考文献重新核查"}]}'

# 运行全套单元测试
pytest -q
```

---

## 2. 命令行接口 (CLI) 选项

命令行入口 `vertical-agent`（在 [pyproject.toml](../packages/agent-harness/pyproject.toml) 中声明指向 `vertical_harness.cli:main`）由 [packages/agent-harness/src/vertical_harness/cli.py](../packages/agent-harness/src/vertical_harness/cli.py) 中的 `_build_parser`、`_print_plugins`、`_print_workflows` 与 `_invoke_prompt` 实现，支持以下核心运行参数：

| 参数 | 说明 | 示例 |
|---|---|---|
| `prompt` | 传递给 Agent 的自然语言任务提示词 | `"请核对手稿引文..."` |
| `--list-plugins` | 打印当前生效的插件白名单元数据 JSON 并退出 | `vertical-agent --list-plugins` |
| `--list-workflows` | 打印已注册的 LangGraph 工作流元数据与三层契约 JSON 并退出 | `vertical-agent --list-workflows` |
| `--history` | 查询并打印指定 `--thread-id` 的检查点历史时间线并退出 | `--thread-id t-1 --history` |
| `--thread-id` | 指定 LangGraph 持久化会话线程 ID（默认生成随机 UUID） | `--thread-id task-1001` |
| `--checkpoint-id` | 指定历史检查点 ID 进行原地回放或状态分叉 (Time Travel) | `--checkpoint-id 1ef...` |
| `--fork-thread-id` | 指定目标分叉新线程 ID，将历史检查点克隆至新分支 | `--fork-thread-id task-fork` |
| `--update-state` | 在检查点回放分叉点注入更新状态的 JSON 对象字符串 | `--update-state '{"messages": ...}'` |
| `--as-node` | 指定 `--update-state` 归属的状态图节点名称 | `--as-node extract_and_validate` |
| `--durability` | 检查点持久化同步级别：`sync`（默认）、`async` 或 `exit` | `--durability sync` |
| `--tenant-id` | 指定多租户隔离标识符（默认 `demo`） | `--tenant-id lab_alpha` |
| `--user-id` | 指定用户隔离标识符（默认 `demo`） | `--user-id researcher_7` |
| `--task-tier` | 任务层级选择：`quality`（高品质型，默认 `TaskTier.QUALITY`）或 `economy`（经济型） | `--task-tier economy` |

---

## 3. 任务路由与文档地图 (Task-Routing Map)

为了帮助开发者和编码智能体快速定位代码与系统设计，全库 Wiki 按照核心系统域组织为以下导航结构：

### 🏗️ 系统架构域 (Architecture)
- [系统架构与核心组装](architecture/overview.md)：理解四大子包分层、Supervisor + LangGraph 工作流插件架构、MCP 异步工具发现与生命周期、`AgentComposer` 与 `aopen_agent` 组装流程、中间件装配、权限沙箱与证据优先系统提示词。
- [插件机制与贡献契约](architecture/plugin-system.md)：掌握基于 Python `entry points` 的动态发现、白名单隔离、`WorkflowContribution` 契约与三层状态隔离、`BaseToolkit` 批量包装及人工审批 (HITL)。
- [状态隔离与分层存储](architecture/state-and-storage.md)：了解会话 Checkpointer、`durability="sync"` 同步落盘、Time Travel Fork 状态分叉、多租户命名空间字符清洗、`SqliteStore` 生命周期及 `CompositeBackend` 虚拟路径路由。
- [动态模型路由与模型适配](architecture/model-routing.md)：探究基于 `wrap_model_call` 与 `RunContext.task_tier` 的经济/品质模型分流机制。

### 🔌 领域插件与集成 (Integrations)
- [学术引用审计插件实现](integrations/citation-plugin.md)：剖析 `vertical-plugin-research-citation` 的 `workflow`（`citation_audit` 确定性状态图）、`checker`/`models` 子包结构、`extract_cited_indices` 与 `evaluate_references` 算法、`ResearchCitationToolkit` 及强约束 `AuditFinding`。

### 🔄 端到端工作流 (Workflows)
- [数字引用审计端到端流程](workflows/citation-audit.md)：跟踪从 CLI 发起任务、Supervisor 委派确定性工作流、`SupervisorPolicyMiddleware` 治理、`SkillsMiddleware` 感知 `SKILL.md` 到 Time Travel 回溯的完整时序。

### ⚙️ 运维与配置 (Operations)
- [环境变量配置与运维部署](operations/configuration.md)：查阅 `VA_*` 环境变量规范、`VA_MCP_SERVERS` 连接配置与传输模式、检查点与工作流模式配置、`SecretStr` 密钥保护机制与 SQLite 存储目标路径推断规则。

### 🧪 测试与质量保证 (Testing)
- [测试架构与质量保证](testing/test-suite.md)：了解 pytest 测试套件、MCP 适配器与 Tool Annotations 测试、Supervisor 工作流与 Time Travel 测试、解析器与 Toolkit 集成验证、防目录遍历攻击断言、严格输入防护与存储持久化回归测试。
