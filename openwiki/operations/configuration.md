---
type: concept
title: 环境变量配置与运维部署
description: 阐述 HarnessSettings 环境变量规范、VA_* 前缀、SecretStr 凭证保护、工作区目录与存储路径解析。
tags: [operations, configuration, pydantic-settings, environment-variables, deployment]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T03:23:43.651Z
sources:
  - id: openwiki-source-6b00a878043c510b9b026ee1
    resource: repo://packages/agent-harness/src/vertical_harness/settings.py
generated: { by: "antigravity", at: "2026-10-18T03:23:43.651Z" }
---

# 环境变量配置与运维部署

`vertical-agent-harness` 采用 Pydantic Settings 实现严格的环境变量解析与类型校验。所有宿主核心配置均通过统一的前缀 `VA_` 进行隔离，杜绝环境变量命名污染，并确保敏感凭证在日志与异常堆栈中不被意外泄露。

## 1. 配置加载与规范 (`settings.py`)

在 [packages/agent-harness/src/vertical_harness/settings.py](../../packages/agent-harness/src/vertical_harness/settings.py) 中，`HarnessSettings` 继承自 `pydantic_settings.BaseSettings`：

```python
class HarnessSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
```

- **显式配置模型 (Fail-Closed)**：`model: str = Field(min_length=1)` 为必填项。根据设计规范，系统绝不使用硬编码的默认模型回退。如果未配置 `VA_MODEL` 环境变量，实例化时将直接抛出 `ValidationError`（见 [tests/test_schema.py](../../tests/test_schema.py)）。
- **凭证安全保护 (`SecretStr`)**：`api_key: SecretStr | None = None` 采用 Pydantic 的 `SecretStr` 类型封装。打印配置对象或序列化时仅显示 `**********`，只有在初始化模型底层连接时才显式调用 `.get_secret_value()` 解包。

## 2. 环境变量对照表

参考 [.env.example](../../.env.example)，核心配置项如下：

| 环境变量 | 类型 / 默认值 | 作用说明 |
|---|---|---|
| `VA_MODEL` | `str` (必填) | 默认主大模型标识符，如 `deepseek:deepseek-flash` |
| `VA_API_KEY` | `SecretStr` (可选) | 模型服务商 API 密钥，避免明文泄露 |
| `VA_BASE_URL` | `str` (可选) | 自定义 API 服务端点（如代理或私有化网关） |
| `VA_ECONOMY_MODEL` | `str` (可选) | 动态路由经济型模型标识符 |
| `VA_QUALITY_MODEL` | `str` (可选) | 动态路由高品质型模型标识符 |
| `VA_WORKSPACE_ROOT` | `Path` (`./.agent-workspace`) | Agent 本地文件系统工作区物理根路径 |
| `VA_STORE_BACKEND` | `Literal["sqlite", "memory"]` (`sqlite`) | 状态存储后端类型 |
| `VA_STORE_DB_PATH` | `Path` / `":memory:"` (可选) | 自定义 SQLite 数据库文件绝对或相对路径 |
| `VA_MEMORY_ROUTE` | `str` (`/memories/`) | 长期记忆在虚拟文件系统中的路由前缀 |
| `VA_ENABLED_PLUGINS` | JSON 字符串 (`'["research.citations"]'`) | 启用的插件白名单列表 |
| `VA_ENABLE_PLANNING` | `bool` (`true`) | 是否启用 `TodoListMiddleware` 规划任务清单 |
| `VA_ENABLE_SKILL_GATING`| `bool` (`true`) | 是否启用技能门控（未激活技能时隐藏工具） |
| `VA_MAX_MODEL_CALLS` | `int` (`30`, 范围 1-500) | 单次执行最大模型调用次数，防止死循环失控 |

## 3. SQLite 存储目标路径推断

`HarnessSettings` 提供了计算属性 `resolved_store_target`，用于自动推断 SQLite 物理落盘位置：

- **显式指定文件路径**：若配置了 `VA_STORE_DB_PATH`，展开并解析为系统绝对路径 `Path(self.store_db_path).expanduser().resolve()`。
- **纯内存数据库**：若配置值为 `":memory:"`，直接返回字符串字面量 `":memory:"`。
- **默认工作区挂载**：若未配置 `VA_STORE_DB_PATH`，默认在已解析的 `workspace_root` 目录下创建 `store.sqlite`。
- **自动化目录创建**：在 `_prepare_conn_string` 中，系统会自动检查并递归创建数据库父目录，避免因目录不存在而抛出 SQLite 错误。

## 4. 生产运维与安全注意事项

1. **凭证隔离**：生产环境中切勿将包含真实密钥的 `.env` 提交至版本库；主包的 `compose.py` 在 `FilesystemPermission` 中配置了强制规则，大模型在任何情况下均被禁止读取或修改 `/**/.env*` 文件。
2. **多进程并发**：当前 CLI 与演示环境适合单进程或轻量开发；生产环境若多进程共享同一工作区，建议将 `store_backend` 指向独立的数据库服务（或由应用层注入共享 Postgres Checkpointer 与 Store）。

## 相关页面

- [快速开始与任务导航](../quickstart.md)：查看从 `.env.example` 复制配置并运行 CLI 的步骤。
- [动态模型路由与模型适配](../architecture/model-routing.md)：了解经济型与品质型模型的配置要求。
- [状态隔离与分层存储](../architecture/state-and-storage.md)：了解 `SqliteStore` 的底层表结构与命名空间。
