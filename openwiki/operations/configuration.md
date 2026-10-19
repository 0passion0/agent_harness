---
type: concept
title: 环境变量配置与运维部署
description: 阐述 HarnessSettings 环境变量规范、VA_* 前缀、SecretStr 凭证保护、工作区目录与存储路径解析。
tags: [operations, configuration, pydantic-settings, environment-variables, deployment]
sources:
  - id: openwiki-source-20e6f612e4230e00fcfd13ac
    resource: repo://packages/agent-harness/src/vertical_harness/config/settings.py
verified:
  - by: openwiki/0.7.2
    at: 2026-10-19T02:30:03.285Z
generated: { by: "antigravity", at: "2026-10-19T02:30:03.285Z" }
---

# 环境变量配置与运维部署

`vertical-agent-harness` 采用 Pydantic Settings 实现严格的环境变量解析与类型校验。所有宿主核心配置集中在 [packages/agent-harness/src/vertical_harness/config/settings.py](../../packages/agent-harness/src/vertical_harness/config/settings.py)，并通过统一的前缀 `VA_` 进行隔离，杜绝环境变量命名污染，并确保敏感凭证在日志与异常堆栈中不被意外泄露。

## 1. 配置加载与规范 (`config/settings.py`)

在 [packages/agent-harness/src/vertical_harness/config/settings.py](../../packages/agent-harness/src/vertical_harness/config/settings.py) 中，定义了枚举 `StoreBackendType(StrEnum)`（`SQLITE = "sqlite"`、`MEMORY = "memory"`）、`TaskTier(StrEnum)`（`ECONOMY = "economy"`、`QUALITY = "quality"`）以及继承自 `pydantic_settings.BaseSettings` 的 `HarnessSettings`：

```python
class HarnessSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
```

- **显式配置模型 (Fail-Closed)**：`model: NonEmptyStr`（即 `Annotated[str, Field(min_length=1)]`）为必填项。根据设计规范，系统绝不使用硬编码的默认模型回退。如果未配置 `VA_MODEL` 环境变量，实例化时将直接抛出 `ValidationError`（见 [tests/test_schema.py](../../tests/test_schema.py)）。
- **凭证安全保护 (`SecretStr` 与 `model_init_kwargs`)**：`api_key: SecretStr | None = None` 采用 Pydantic 的 `SecretStr` 类型封装。打印配置对象或序列化时仅显示掩码，只有在访问计算属性 `model_init_kwargs` 初始化模型底层连接时才显式调用 `self.api_key.get_secret_value()` 解包并过滤非空 `base_url`。
- **动态路由成对校验 (`has_dynamic_routing`)**：当配置 `economy_model` 或 `quality_model` 时，`has_dynamic_routing` 属性验证两者是否同时设置；若仅配置其中之一则抛出 `ValueError("Both VA_ECONOMY_MODEL and VA_QUALITY_MODEL must be set")`。

## 2. 环境变量对照表

参考 [.env.example](../../.env.example)，核心配置项如下：

| 环境变量 | 类型 / 默认值 | 作用说明 |
|---|---|---|
| `VA_MODEL` | `NonEmptyStr` (必填) | 默认主大模型标识符，如 `deepseek:deepseek-flash` |
| `VA_API_KEY` | `SecretStr` (可选) | 模型服务商 API 密钥，避免明文泄露 |
| `VA_BASE_URL` | `str` (可选) | 自定义 API 服务端点（如代理或私有化网关） |
| `VA_ECONOMY_MODEL` | `NonEmptyStr` (可选) | 动态路由经济型模型标识符 |
| `VA_QUALITY_MODEL` | `NonEmptyStr` (可选) | 动态路由高品质型模型标识符 |
| `VA_WORKSPACE_ROOT` | `Path` (`./.agent-workspace`) | Agent 本地文件系统工作区物理根路径 |
| `VA_RUNTIME_DATA_DIR` | `Path` (可选) | 运行时持久化数据隔离目录，默认在工作区同级目录创建 `.runtime` 目录 |
| `VA_STORE_BACKEND` | `Literal["sqlite", "memory"]` (`sqlite`) | 状态存储后端类型（默认 `StoreBackendType.SQLITE`） |
| `VA_STORE_DB_PATH` | `Path` / `":memory:"` (可选) | 自定义 SQLite 数据库文件绝对或相对路径 |
| `VA_CHECKPOINT_DB_PATH` | `Path` / `":memory:"` (可选) | 自定义 LangGraph Checkpointer 检查点 SQLite 数据库路径 |
| `VA_DURABILITY` | `Literal["sync", "async", "exit"]` (`sync`) | 检查点持久化同步级别，默认 `sync` 确保每步同步落盘以支持 Time Travel / Fork |
| `VA_WORKFLOW_MODE` | `Literal["compiled", "async"]` (`compiled`) | 工作流插件接入模式：`compiled`（本地预编译图）或 `async`（Agent Protocol `graph_id`） |
| `VA_SUPERVISOR_ONLY` | `bool` (`false`) | 是否启用纯监督者模式（仅委派工作流，屏蔽直接业务工具与 general-purpose 子智能体） |
| `VA_MEMORY_ROUTE` | `MemoryRouteStr` (`/memories/`) | 长期记忆在虚拟文件系统中的路由前缀（正则 `^/[a-zA-Z0-9_\-]+/$`） |
| `VA_ENABLED_PLUGINS` | JSON 字符串 (`'["research.citations"]'`) | 启用的插件白名单列表 |
| `VA_MCP_SERVERS` | JSON 字符串 (可选) | MCP 服务名到连接参数映射（支持 `stdio` 与 `streamable_http` 传输） |
| `VA_ENABLE_PLANNING` | `bool` (`true`) | 是否启用 `TodoListMiddleware` 规划任务清单 |
| `VA_ENABLE_SKILL_GATING`| `bool` (`true`) | 是否启用技能门控（未激活技能时隐藏工具） |
| `VA_MAX_MODEL_CALLS` | `ModelCallLimit` (`30`, 范围 1-500) | 单次执行最大模型调用次数，防止死循环失控 |
| `VA_LOG_LEVEL` | `Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]` (`INFO`) | 应用日志等级（输出至 stderr） |
| `VA_LOG_FORMAT` | `Literal["text", "json"]` (`text`) | 日志格式：单行 key=value (`text`) 或结构化 JSON (`json`) |
| `VA_LOG_FILE` | `Path` (可选) | 可选的 UTF-8 编码滚动日志文件路径 |
| `VA_LOG_MAX_BYTES` | `int` (`10485760`, 10MB) | 滚动日志单文件最大字节数 |
| `VA_LOG_BACKUP_COUNT` | `int` (`5`) | 滚动日志备份文件保留数量 |

## 3. 路径解析与 SQLite 存储目标推断

`HarnessSettings` 提供了多个规范化计算属性：

- **`resolved_workspace_root`**：展开用户目录并解析为工作区绝对物理路径 `self.workspace_root.expanduser().resolve()`。
- **`resolved_runtime_data_dir`**：解析运行时数据目录绝对路径；未显式指定时，默认在工作区同级目录创建带有 `.runtime` 后缀的隔离目录（`root.with_name(f"{root.name}.runtime")`），将数据库与用户业务文件完全隔开。
- **`resolved_store_target`**：自动推断 Long-term Store SQLite 物理落盘位置：
  - **默认隔离数据目录挂载**：若 `store_db_path is None`，检查旧版未隔离数据库后默认返回 `self.resolved_runtime_data_dir / "store.sqlite"`。
  - **纯内存数据库**：若配置值为 `":memory:"`，直接返回字符串字面量 `":memory:"`。
  - **显式指定文件路径**：其余情况展开并解析为绝对路径 `Path(self.store_db_path).expanduser().resolve()`。
- **`resolved_checkpoint_target`**：自动推断 Checkpointer 检查点 SQLite 物理落盘位置：
  - **默认隔离数据目录挂载**：若 `checkpoint_db_path is None`，且 `store_backend != "memory"`，默认返回 `self.resolved_runtime_data_dir / "checkpoints.sqlite"`；若存储为内存模式则回退为 `":memory:"`。
  - **显式配置**：解析为绝对路径或 `":memory:"`。
- **`normalized_memory_route`**：通过 `PurePosixPath` 将 `memory_route` 规范化为前后均带单斜杠的 POSIX 虚拟目录格式（如 `"/memories/"`）。

## 4. 生产运维与安全注意事项

1. **凭证隔离**：生产环境中切勿将包含真实密钥的 `.env` 提交至版本库；主包的 [engine/compose.py](../../packages/agent-harness/src/vertical_harness/engine/compose.py) 在 `DEFAULT_FILESYSTEM_PERMISSIONS` 中配置了强制规则，大模型在任何情况下均被禁止读取或修改 `/**/.env` 与 `/**/.env.*` 文件。
2. **多进程并发**：当前 CLI 与演示环境适合单进程或轻量开发；生产环境若多进程共享同一工作区，建议将 `store_backend` 指向独立的数据库服务（或由应用层注入共享 Postgres Checkpointer 与 Store）。
3. **桌面端运行时兼容**：桌面前端工作台（`desktop/`）通过 `desktop/runtime.py` 复用 `HarnessSettings`，并在此基础上增加了基于账户哈希的应用数据独立隔离目录。

## 相关页面

- [快速开始与任务导航](../quickstart.md)：查看从 `.env.example` 复制配置并运行 CLI 的步骤。
- [动态模型路由与模型适配](../architecture/model-routing.md)：了解经济型与品质型模型的配置要求。
- [状态隔离与分层存储](../architecture/state-and-storage.md)：了解 `SqliteStore` 的底层表结构与命名空间。
- [MedPeer 桌面前端工作台](../integrations/desktop-workbench.md)：了解桌面工作台运行时的专属环境配置。
