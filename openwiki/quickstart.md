---
type: concept
title: 快速开始与任务导航
description: Agent Harness 快速启动指南、环境安装、CLI 使用方法、桌面前端启动及验证基线。
tags: [quickstart, navigation, installation, cli, overview, desktop]
sources:
  - id: openwiki-source-68add2df392f9f491f54a7b3
    resource: repo://packages/agent-harness/pyproject.toml
  - id: openwiki-source-3738efb5862ea362ae17bac1
    resource: repo://packages/agent-harness/src/vertical_harness/cli.py
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
verified:
  - by: openwiki/0.7.2
    at: 2026-10-21T02:48:18.086Z
generated: { by: "antigravity", at: "2026-10-21T02:48:18.086Z" }
---

# 快速开始与任务导航

欢迎阅读 `vertical-agent-harness` 知识库。本仓库基于 `deepagents.create_deep_agent` 构建单一主智能体，具备网络检索、工作区文件读写、受控 Python 脚本执行及原生子任务委派能力，并集成了开箱即用的 Electron / React 桌面前端工作台。

---

## 1. 5 分钟快速上手

### 1.1 环境初始化与安装

推荐使用 `uv` 管理虚拟环境与依赖：

```powershell
# 1. 同步安装所有包及开发、桌面依赖
uv sync --all-packages --extra dev --extra desktop

# 2. 复制并编辑环境变量
Copy-Item .env.example .env
# 编辑 .env，确保配置 VA_MODEL (如 deepseek:deepseek-chat) 与 VA_API_KEY
```

### 1.2 命令行 (CLI) 运行验证

通过 `vertical-agent` 命令行交互：

```powershell
# 执行工作区查询任务
uv run vertical-agent "检查当前工作区文件"

# 传入本地图像进行多模态分析
uv run vertical-agent --image ./sample.png "描述图片内容"

# 指定会话 ID
uv run vertical-agent --thread-id my-session-1 "分析当前目录"
```

### 1.3 启动桌面前端工作台

桌面端基于 Electron + React 构建，并通过标准输入输出 JSON-RPC 2.0 对接 Python 桌面后端：

```powershell
# 进入 desktop 目录并启动开发服务
cd desktop
npm ci
npm run dev
```

---

## 2. 命令行接口 (CLI) 选项

命令行入口 `vertical-agent`（在 [pyproject.toml](../packages/agent-harness/pyproject.toml) 中声明指向 `vertical_harness.cli:Cli.main`）由 [packages/agent-harness/src/vertical_harness/cli.py](../packages/agent-harness/src/vertical_harness/cli.py) 实现，支持以下参数：

| 参数 | 说明 | 示例 |
|---|---|---|
| `prompt` | 传递给智能体的自然语言任务提示词 | `"总结当前项目报告"` |
| `--image` | 附加本地图像（PNG/JPEG/GIF/WebP），支持多次传入 | `--image ./figure1.png` |
| `--thread-id` | 会话唯一标识符（默认自动生成 UUID） | `--thread-id session-101` |
| `--user-id` | 租户用户标识（默认 `default`） | `--user-id user_42` |
| `--tenant-id` | 多租户标识（默认 `default`） | `--tenant-id tenant_med` |

---

## 3. 质量验证基线

修改代码后必须在项目虚拟环境中执行全套验证：

```powershell
# Python 单元测试
.\.venv\Scripts\python -m pytest

# 代码风格与静态规范检查
.\.venv\Scripts\python -m ruff check .

# 核心模块类型检查
.\.venv\Scripts\python -m mypy

# 桌面端验证 (在 desktop 目录下)
npm run typecheck
npm test
npm run build
```

---

## 4. 全库知识导航

- **核心架构**：[Harness 系统架构与核心组装](architecture/overview.md)
- **模型路由**：[动态模型路由与模型适配](architecture/model-routing.md)
- **存储与文档**：[状态管理、检查点与文档后端](architecture/state-and-storage.md)
- **桌面前端**：[MedPeer 桌面前端工作台](integrations/desktop-workbench.md)
- **运行配置**：[运行配置与环境参数](operations/configuration.md)
- **测试体系**：[测试体系与质量验证](testing/test-suite.md)
