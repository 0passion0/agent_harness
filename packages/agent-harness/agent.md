# Agent Harness 包目录 (packages/agent-harness) - Agent 指南与规范

## 目录定位与职责
`packages/agent-harness/` 是 Vertical Agent Harness 的核心代码库，发布为 Python 包 `vertical-agent-harness`。它同时包含宿主框架 `vertical_harness` 与内置业务插件 `vertical_plugins`。它封装了生产级智能体运行时，支撑多模型接入、长周期任务中断与恢复、动态模型路由、安全沙箱及结构化日志。

## 目录结构
- `src/`：包源码根目录，包含平级组织的 `vertical_harness`（宿主运行时）与 `vertical_plugins`（业务插件与契约）。
- `pyproject.toml`：本包的项目定义、依赖配置、可选依赖组（`analysis`、`postgres`、`dev`）、entry points 与 wheel 构建配置。

## 核心设计模式
1. **单一组装点（Single Point of Assembly）**：通过 `vertical_harness.engine.compose` 完成模型、中间件、存储后端和插件贡献的统一校验与装配。
2. **插件 ABC 契约隔离**：所有插件继承 `vertical_plugins.common.DomainPlugin` ABC 抽象基类，根目录严格仅有一个 `__init__.py` 文件，通过 `build(services)` 注入贡献。
3. **多租户与命名空间隔离**：持久化状态和全局存储在物理或逻辑层面严格依照 `tenant_id` 和 `user_id` 实施命名空间隔离。
4. **安全证据优先**：默认注入严格证据链提示词，并在监督模式下禁用危险的文件系统工具。

