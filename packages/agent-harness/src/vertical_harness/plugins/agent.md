# 插件模块 (vertical_harness/plugins) - Agent 指南与规范

## 目录定位与职责
`vertical_harness/plugins/` 目录定义了系统面向垂直领域的插件拓展契约标准（Plugin Contracts）以及基于 Python Entry Points 的插件自动发现机制。实现主系统与业务逻辑的彻底解耦。

## 核心组件与文件
- `contracts.py`：插件系统核心类型定义：
  - `DomainPlugin`：插件主入口抽象基类，规范 `manifest` 与 `build()` 方法。
  - `PluginManifest`：插件元数据模型（标识符、版本号、作者、依赖关系等）。
  - `PluginContribution`：插件对宿主的贡献集合（工作流、工具、子智能体、技能资源、中间件）。
  - `WorkflowContribution`：单个 LangGraph 工作流插件的注册契约，定义三层状态模型、上下文模式与持久化要求。
  - `ToolContribution`：工具贡献模型，声明工具的即时暴露或技能门控属性。
  - `BuildServices` / `RunContext`：宿主向插件注入的编译期服务与运行期可信上下文。
- `discovery.py`：
  - `discover_plugins()`：扫描 `vertical_harness.plugins` 入口点，根据允许列表加载并验证插件实例。

## 关键约束与安全规范
1. **命名空间前缀保护**：插件导出的工具、子智能体和技能名称不得使用框架保留前缀或覆盖系统内置保留名称（如 `general-purpose`）。
2. **三层状态契约**：插件工作流必须显式声明输入模式（Input Schema）、内部工作状态（State Schema）与输出模式（Output Schema）。
3. **插件加载防污染**：单个插件加载或构建失败时，必须记录明确的错误日志，不得导致未捕获的静默损坏。
