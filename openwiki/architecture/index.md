# 文件

- [动态模型路由与模型适配](model-routing.md) - 阐述基于 LangChain wrap_model_call 与 RunContext.task_tier 的经济型与高品质模型动态分流机制，以及 HarnessProfile 约束。
- [Harness 系统架构与核心组装](overview.md) - 阐述 Deep Agents 垂直领域 Harness 的子包分层设计、AgentComposer 与 build_agent 组装流程、中间件链条与安全沙箱权限控制。
- [插件机制与贡献契约](plugin-system.md) - 深入解析基于 Python entry points 的无侵入插件发现机制、白名单安全校验、Pydantic 贡献契约、BaseToolkit 适配及技能门控与 HITL。
- [状态隔离与分层存储](state-and-storage.md) - 剖析短时会话 Checkpointer、多租户命名空间 SHA-256 身份隔离、SqliteStore 持久化生命周期以及 CompositeBackend 虚拟文件系统路由机制。
