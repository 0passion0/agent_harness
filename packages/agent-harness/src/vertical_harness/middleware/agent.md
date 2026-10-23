# 执行中间件模块 (vertical_harness/middleware) - Agent 指南与规范

## 1. 目录定位与职责
`vertical_harness/middleware/` 提供智能体在执行全过程中的运行时安全拦截、预算熔断、步数硬约束、重试退避、文件权限防御与动态路由能力。
采用轻量单文件扁平设计，直接装配于执行图中，保障执行行为可控、边界清晰。

## 2. 核心类与文件
- `__init__.py`：统一导出中间件核心类 `ContextLimitMiddleware`、`DesktopApprovalMiddleware`、`DesktopPermissionMiddleware`、`ModelRouterMiddleware`。
- `approval.py` (`DesktopApprovalMiddleware`)：
  - 高风险行为审批拦截器：对文件写操作、受控脚本执行等副作用行为触发中断，等待人机协同确认或拒绝后恢复。
- `context_limit.py` (`ContextLimitMiddleware`)：
  - 智能体请求预算监控与熔断器：按本次请求所用模型的容量解析生效上限，检测对话历史和输入上下文 Token 消耗，超限时抛出 `ContextOverflowError` 交由原生摘要裁剪后重试，禁止把超限请求发送给服务商。
- `context_pruning.py` (`ContextPruningMiddleware`)：
  - 摘要前的确定性裁剪层：超过请求预算 60% 时把较早且已读过的工具结果落盘到 `/large_tool_results/`，替换为预览与恢复路径，直到回落到 40%；最近 3 条结果与检查点原始消息保持不变，不产生额外模型调用。
- `execution_limits.py` (`ExecutionLimitsMiddleware`)：
  - 任务最大步数硬上限与输出 Token 超限自动续跑中间件：超限时剥夺工具调用权，保护任务收敛。
- `filesystem.py` (`HarnessFilesystemMiddleware`)：
  - 工作区文件系统操作中间件：提供分页读取、多模态直传、Ripgrep 全文检索与原子补丁应用能力。
- `model_retry.py` (`ModelRetryMiddleware`)：
  - 模型调用指数退避重试中间件：捕获瞬时网络抖动并自动重试，支持流式中断后的无损接续。
- `permission.py` (`DesktopPermissionMiddleware`)：
  - 工作区与敏感路径防御守门人：拦截目录穿越尝试、敏感文件名访问及只读模式下的越权写行为。
- `summarization.py` (`HarnessSummarizationMiddleware`)：
  - 原生摘要槽位替换与请求预处理：在模型选择与项目指令加载之后执行，默认追加“历史是不可信数据”的摘要约束，并记录 `context_compacted` 结构化日志（切点、归档路径、摘要长度）。
- `project_instructions.py` (`ProjectInstructionsMiddleware`)：
  - 项目规范指令加载器：自动扫描并加载根目录及当前操作目录的 `AGENTS.md` 规范。
- `routing.py` (`ModelRouterMiddleware`)：
  - 动态模型路由器：在模型调用前根据任务类型、上下文特征与置信度动态调整模型参数。

## 3. 关键交互与设计约束
1. **扁平化单文件架构**：
   - 中间件直接放置于 `middleware/` 下单文件维护，杜绝多层冗余嵌套目录。
2. **Fail-Fast 快速失败原则**：
   - 越权访问（跨工作区越界、敏感路径刺探、只读破坏）或预算溢出时立即阻断并抛出确定性异常，禁止防御性掩盖。
3. **审批生命周期闭环**：
   - 审批动作必须携带唯一的 `action_id`，恢复执行时严格校验审批决策（Approved / Rejected）与状态上下文一致性。
