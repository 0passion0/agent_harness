# 引擎模块 (vertical_harness/engine) - Agent 指南与规范

## 目录定位与职责
`vertical_harness/engine/` 是智能体装配与运行调度的核心驱动引擎。负责连接 Deep Agents 与 LangGraph，聚合各领域插件的贡献，管理执行生命周期、动态模型路由、安全中间件策略、流式输出与时间旅行（Time Travel）。

## 核心组件与文件
- `compose.py`：唯一组装点。包含 `AgentComposer` 类与 `build_agent`、`aopen_agent` 等上下文函数，负责验证插件工作流图的无环依赖并构建最终的 CompiledStateGraph。
- `callbacks.py`：包含 `WorkflowLoggingHandler`，将 LangChain 的 chain/llm/tool 执行事件映射为可观测的树状操作跟踪事件。
- `mcp.py`：实现 MCP 工具的异步发现与生命周期管理，提供工具隔离暴露。
- `memory.py`：提供 `UserMemoryMiddleware` 与后台长期记忆定期归纳工作进程（`run_memory_worker`）。
- `policy.py`：实现 `SupervisorPolicyMiddleware` 监督策略，拦截非法直接调用，注入人机协同审批（Human-in-the-loop）中断点。
- `routing.py`：提供原生 LangChain 模型动态路由器（`make_router`），根据运行时 `TaskTier` 动态分配经济型或品质型模型。
- `streaming.py`：提供统一的异步事件流式发射器，适配前端流式渲染。
- `time_travel.py`：基于 LangGraph 检查点机制实现历史状态查询、检查点重放（Replay）与分叉恢复（Fork）。

## 关键约束与安全规范
1. **工作流依赖无环性**：组装时必须深度遍历工作流的 `depends_on` 依赖关系，发现循环依赖时立即抛出 `ValueError`。
2. **中间件顺序确定性**：遵循严格的中间件注入顺序（安全策略 -> 模型路由 -> 记忆注入 -> 技能管理 -> 插件自定义）。
3. **敏感信息过滤**：回调处理器和引擎执行日志严禁记录用户 Prompt 明文与工具执行的私密参数。
