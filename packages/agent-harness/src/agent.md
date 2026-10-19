# Agent Harness 源码根目录 (packages/agent-harness/src) - Agent 指南与规范

## 目录定位与职责
`packages/agent-harness/src/` 是核心包的源代码根目录，采用标准 `src` 布局以避免环境污染和隐式导入问题。

## 命名空间组织
- `vertical_harness/`：核心宿主模块目录，对外导出标准 API：
  - `cli.py`：命令行交互入口。
  - `logging.py`：结构化日志配置与上下文。
  - `observability.py`：链路追踪与度量收集。
  - `config/`：配置定义与 MCP 服务配置。
  - `engine/`：智能体组装、路由、执行、时间旅行及插件加载器（`plugin_loader.py`）。
  - `evaluation/`：离线/在线评估体系。
  - `storage/`：持久化存储后端与命名空间隔离。
- `vertical_plugins/`：领域业务插件与公共契约层：
  - `common/`：抽象基类 `DomainPlugin` (ABC)、贡献类型契约与公共提示词拼装。
  - `data_analysis/`：科研数据分析插件（根目录仅 `__init__.py`，子包划分 `workflow/`, `tools/`, `models/`, `prompts/`, `runtime/`, `guides/`, `skills/`）。
  - `research_citation/`：学术引文核验插件（根目录仅 `__init__.py`，子包划分 `workflow/`, `checker/`, `models/`, `prompts/`, `skills/`）。

## 开发与维护规则
1. **统一入口导出**：所有关键类和函数均需在顶层 `vertical_harness.__init__` 中显式导出并在 `__all__` 中列出。
2. **导入分层清晰与架构隔离**：
   - 通用宿主 `vertical_harness` 和公共层 `vertical_plugins.common` 绝不直接依赖具体业务插件。
   - 业务插件根目录严格只允许 `__init__.py` 单一 Python 文件。
3. **日志规范执行**：所有模块内部均应初始化独立 `logger = logging.getLogger(__name__)`，并在函数中添加完备结构化日志。

