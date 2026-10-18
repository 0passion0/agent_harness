# 包管理目录 (packages) - Agent 指南与规范

## 目录定位与职责
`packages/` 是整个项目的多包（Monorepo）工作区根目录，通过 `uv` 统一管理。该目录下汇集了核心引擎框架包和各类领域垂直插件包。

## 子包清单
- `agent-harness/`：核心宿主脚手架（Vertical Agent Harness），提供通用的智能体执行环境、中间件、存储集成、评估管线与 CLI。
- `research-citation/`：学术文献与引文核验领域插件，展示了基于插件契约扩展特定垂直领域业务能力的参考实现。

## Agent 交互与跨包依赖规范
1. **工作区解耦原则**：
   - `packages/agent-harness` 作为底层宿主，绝对不可显式依赖具体的垂直插件包（如 `research-citation`）。
   - 垂直插件包（如 `research-citation`）依赖 `agent-harness` 提供的契约接口（`vertical_harness.plugins.contracts`）和工具模型。
2. **依赖管理**：所有包的内部依赖均在各自的 `pyproject.toml` 中通过 workspace 路径定义，并通过根目录 `pyproject.toml` 的 `[tool.uv.workspace]` 统一声明。
3. **版本演进**：修改公共接口契约时，需保证语义化版本兼容，避免破坏下游插件的注册与调度。
