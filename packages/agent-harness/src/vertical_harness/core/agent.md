# 装配与基础契约

AgentComposer 只装配原生图，模型选择由 ModelRouting 负责，RunContext 保持可信身份契约。

- 运行选项统一来自 HarnessSettings，不接收另一个 options 字典。
- 权限生成、中间件构造和图装配分别处理。
- 请求准备按路由、指令、裁剪顺序执行，再进入原生摘要及预算检查。
- 主任务与原生子任务共享权限、技能、审批和预算；只读模式禁用委派。
- open_agent/aopen_agent 创建的资源由自身关闭；显式借用的 Shell 执行器由调用方关闭。
