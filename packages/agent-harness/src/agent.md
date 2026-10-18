# Agent Harness 源码根目录 (packages/agent-harness/src) - Agent 指南与规范

## 目录定位与职责
`packages/agent-harness/src/` 是核心包的源代码根目录，采用标准 `src` 布局以避免环境污染和隐式导入问题。

## 命名空间组织
- `vertical_harness/`：核心模块目录，对外导出标准 API：
  - `cli.py`：命令行交互入口。
  - `logging.py`：结构化日志配置与上下文。
  - `observability.py`：链路追踪与度量收集。
  - `config/`：配置定义与 MCP 服务配置。
  - `engine/`：智能体组装、路由、执行与时间旅行。
  - `evaluation/`：离线/在线评估体系。
  - `plugins/`：插件系统契约与自动发现。
  - `storage/`：持久化存储后端与命名空间隔离。

## 开发与维护规则
1. **统一入口导出**：所有关键类和函数均需在顶层 `vertical_harness.__init__` 中显式导出并在 `__all__` 中列出。
2. **导入分层清晰**：禁止底层模块（如 `storage`）反向引用高层模块（如 `engine`）。
3. **日志规范执行**：所有模块内部均应初始化独立 `logger = logging.getLogger(__name__)`，并在函数中添加完备结构化日志。
