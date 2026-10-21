# 配置模块 (vertical_harness/config) - Agent 指南与规范

## 1. 目录定位与职责
`vertical_harness/config/` 负责智能体微框架全生命周期的类型化配置定义、校验与加载。
基于 Pydantic 强类型模型实现环境变量注入、默认值兜底、层级任务划分与可观测性参数管理。

## 2. 核心类与文件
- `__init__.py`：统一导出配置公共类 `CheckpointConfig`、`HarnessSettings`、`LangSmithSettings`、`StoreBackendType`、`TaskTier`。
- `settings.py` (`HarnessSettings`)：
  - 核心运行时配置模型，统合模型标识、推理温度、工作区根目录、敏感路径规则及只读模式标志。
- `checkpoint_config.py` (`CheckpointConfig`)：
  - 智能体对话会话标识与检查点持久化配置（`thread_id`、`checkpoint_ns`）。
- `langsmith_settings.py` (`LangSmithSettings`)：
  - LangSmith 链路追踪参数配置（API 密钥、项目名称、追踪开关与端点）。
- `store_backend.py` (`StoreBackendType`)：
  - 键值状态存储后端类型枚举（`MEMORY`、`SQLITE`）。
- `task_tier.py` (`TaskTier`)：
  - 任务性能与成本分级枚举（`ECONOMY`、`QUALITY`）。

## 3. 关键交互与设计约束
1. **环境变量前缀规范**：
   - 宿主配置统一且仅读取 `VA_*` 前缀的环境变量（如 `VA_MODEL`、`VA_WORKSPACE_DIR`），避免命名空间冲突。
2. **凭据安全与字段脱敏**：
   - 包含敏感信息的字段（如 `api_key`）严禁在日志打印或错误堆栈中明文展示。
3. **路径合法性与绝对化**：
   - 工作区目录与敏感规则必须在加载后统一转为规范绝对路径，防止路径遍历与相对路径歧义。
4. **强类型与 Fail-Fast**：
   - 配置字段必须具备明确类型注解，遇到非法配置立即抛出校验异常，禁止静默掩盖配置错误。
