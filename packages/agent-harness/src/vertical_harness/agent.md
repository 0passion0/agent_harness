# Vertical Harness 核心命名空间 (vertical_harness) - Agent 指南与规范

## 目录定位与职责
`vertical_harness/` 是整个智能体宿主脚手架的核心模块所在目录，统一提供配置管理、运行引擎组装、模型路由、中间件管控、结构化日志及持久化存储等全部基础设施。

## 核心文件与模块
- `__init__.py`：统一公共 API 导出网关，显式暴露 `HarnessSettings`、`build_agent`、`configure_logging`、`DomainPlugin` 等核心类和方法。
- `cli.py`：交互式命令行工具，支持命令解析、提示词调用、多模态附件解析、工作流/插件列举及时间旅行检查点复现。
- `logging.py`：生产级结构化日志体系，支持 JSON/KV 双格式输出、调用上下文追踪（`run_id` 注入）及严格的敏感信息屏蔽脱敏。
- `observability.py`：链路追踪集成，支持 LangSmith 与 OpenTelemetry 上下文管理。
- `config/`：类型化配置模型与环境变量加载。
- `engine/`：Deep Agents 组装器、模型路由、时间旅行、回调处理与记忆工作进程。
- `evaluation/`：端到端与单项离线/在线评测流水线。
- `plugins/`：插件协议契约接口定义与动态加载机制。
- `storage/`：Checkpointer 和 BaseStore 存储后端管理及多租户隔离。

## 关键交互与设计原则
1. **统一生命周期管理**：推荐使用上下文管理器（如 `open_store`、`aopen_agent`）确保文件句柄、网络连接与后台任务优雅关闭。
2. **零隐私泄漏**：绝对不可在未经脱敏的日志流中输出用户原始文本与密钥。
3. **高内聚低耦合**：各子模块通过明确定型的接口进行数据交换，严禁隐式全局状态。
