# 核心业务与编排模块 (vertical_harness/core) - Agent 指南与规范

## 1. 目录定位与职责
`vertical_harness/core/` 是智能体宿主的核心组装与控制中枢。
负责将底层 `deepagents.create_deep_agent`、工作区文件系统后端、网络检索、受控脚本执行工具与多维度安全中间件栈组合成完整的单一执行智能体，并提供模型动态路由与请求上下文管理。

## 2. 核心类与文件
- `__init__.py`：统一导出核心业务类 `AgentComposer`、`ModelRouting`、`RunContext`。
- `compose.py` (`AgentComposer`)：
  - 智能体总装配器：构建包含 `FilesystemMiddleware`、`SummarizationMiddleware`（90% 预算压缩）、`ContextLimitMiddleware`、`DesktopPermissionMiddleware`、`DesktopApprovalMiddleware`、`TodoListMiddleware` 和 `ModelRouterMiddleware` 的中间件栈。
  - 装配网络检索、受控 Python 脚本执行及用户交互确认工具。
  - 配置原生子任务委派（subagents），保证子智能体继承工作区权限与审批约束。
- `routing.py` (`ModelRouting`)：
  - LangChain ChatModel 实例构建与路由器，按配置动态匹配供应商与推理参数。
- `context.py` (`RunContext`)：
  - 不可变运行上下文载体，包含调用者标识、租户、会话标识、工作区绝对路径及追踪元数据。

## 3. 关键交互与设计约束
1. **单一智能体与框架原生复用**：
   - 严格基于 `create_deep_agent` 构建执行图，不自研复杂状态机或额外工作流引擎；严禁引入业务插件、MCP、技能注册或长期记忆。
2. **子任务权限与审批继承**：
   - 原生子任务委派执行时，必须继承主智能体的工作区范围、敏感路径拒绝列表、人工审批机制与上下文配额。
3. **接口类封装纯粹性**：
   - 所有业务能力通过 `AgentComposer` 显式方法对外提供，严禁暴露模块级函数别名或旧模块（如 `engine/`）转发层。
4. **Prompt 与系统边界清晰**：
   - 基础提示词（`BASE_PROMPT`）约束智能体遵循事实核验、局部修改（`edit_file`）、非受信输入原则与受控脚本验证流程。
