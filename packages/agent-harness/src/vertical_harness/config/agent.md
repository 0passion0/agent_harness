# 配置模块 (vertical_harness/config) - Agent 指南与规范

## 目录定位与职责
`vertical_harness/config/` 负责定义和解析智能体运行时的全部配置项，基于 Pydantic Settings 实现强类型校验、默认值兜底与环境变更感知。

## 核心组件与文件
- `__init__.py`：导出配置相关的公共类型与设置类。
- `settings.py`：
  - `HarnessSettings`：主配置模型，统一读取 `VA_*` 前缀的环境变量，包含模型选择、存储类型、多租户参数、日志配置、MCP 配置及插件列表。
  - `TaskTier`：任务质量与成本分级枚举（`ECONOMY`、`QUALITY`）。
  - `StoreBackend`：持久化存储类型枚举（`MEMORY`、`SQLITE`、`POSTGRES`）。
  - `WorkflowMode`：工作流执行模式枚举（`STANDALONE`、`DELEGATED`、`ALL`）。
- `mcp.py`：
  - `MCPServerSettings`：单个 MCP 服务的连接配置（命令、参数、环境变量、传输协议类型）。
  - `MCPSettings`：多 MCP 服务的集合管理模型。

## 关键约束与安全规范
1. **环境变量前缀隔离**：所有宿主框架配置必须且仅使用 `VA_` 前缀（如 `VA_MODEL`、`VA_STORE_BACKEND`）。
2. **凭据安全保护**：包含 API 密钥的字段（如 `api_key`、`headers`）必须使用 `SecretStr` 或私有过滤序列化，禁止直接明文打印。
3. **路径安全解析**：所有传入的文件路径必须通过 `.resolve()` 和工作区根目录核验，防止目录穿越攻击。
