# 包管理目录 (packages) - Agent 指南与规范

## 目录定位与职责
`packages/` 是整个项目的工作区包目录，通过 `uv` 统一管理。项目收敛为单一发行包 `packages/agent-harness`（分发名称 `vertical-agent-harness`），内部平级组织通用宿主 `vertical_harness` 与领域垂直插件 `vertical_plugins`。

## 包清单
- `agent-harness/`：Vertical Agent Harness 发行包源码，包含：
  - `src/vertical_harness/`：通用宿主脚手架，提供智能体执行环境、中间件、存储集成、评估管线与 CLI。
  - `src/vertical_plugins/`：领域插件与公共契约层，包含 `common`（ABC 契约）、`data_analysis`（科研数据分析插件）与 `research_citation`（学术引文核验插件）。

## 架构隔离与依赖规范
1. **解耦原则**：
   - 通用宿主 `vertical_harness` 和公共契约层 `vertical_plugins.common` 绝对禁止显式导入具体业务插件（如 `data_analysis` 或 `research_citation`）。
   - 业务插件继承 `vertical_plugins.common.DomainPlugin` ABC 契约，并通过 `build(services) -> PluginContribution` 向宿主统一注册贡献。
   - 宿主通过 `vertical_harness.engine.plugin_loader` 依据 entry point 动态发现并装配启用的白名单插件。
2. **依赖管理**：基础安装不依赖可选数据分析库；`duckdb` 与 `openpyxl` 作为 `analysis` extra 可选依赖。
3. **版本演进**：公共契约修改必须保持向后兼容。

