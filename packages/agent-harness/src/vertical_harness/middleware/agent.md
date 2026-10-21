# 执行中间件模块 (vertical_harness/middleware) - Agent 指南与规范

## 1. 目录定位与职责
`vertical_harness/middleware/` 提供智能体在执行全过程中的运行时安全拦截、预算熔断、权限防御与动态路由能力。
采用轻量单文件扁平设计，直接装配于执行图中，保障执行行为可控、边界清晰。

## 2. 核心类与文件
- `__init__.py`：统一导出中间件核心类 `ContextLimitMiddleware`、`DesktopApprovalMiddleware`、`DesktopPermissionMiddleware`、`ModelRouterMiddleware`。
- `approval.py` (`DesktopApprovalMiddleware`)：
  - 高风险行为审批拦截器：对文件写操作、受控脚本执行等副作用行为触发中断，等待人机协同确认或拒绝后恢复。
- `context_limit.py` (`ContextLimitMiddleware`)：
  - 智能体请求预算监控与熔断器：检测对话历史和输入上下文 Token 消耗，超限时主动截断并保护系统不致雪崩。
- `permission.py` (`DesktopPermissionMiddleware`)：
  - 工作区与敏感路径防御守门人：拦截目录穿越尝试、敏感文件名访问及只读模式下的越权写行为。
- `routing.py` (`ModelRouterMiddleware`)：
  - 动态模型路由器：在模型调用前根据任务类型、上下文特征与置信度动态调整模型参数。

## 3. 关键交互与设计约束
1. **扁平化单文件架构**：
   - 四个中间件直接放置于 `middleware/` 下单文件维护，杜绝多层冗余嵌套目录。
2. **Fail-Fast 快速失败原则**：
   - 越权访问（跨工作区越界、敏感路径刺探、只读破坏）或预算溢出时立即阻断并抛出确定性异常，禁止防御性掩盖。
3. **审批生命周期闭环**：
   - 审批动作必须携带唯一的 `action_id`，恢复执行时严格校验审批决策（Approved / Rejected）与状态上下文一致性。
