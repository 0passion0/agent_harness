# LangChain 与流式分发模块 (vertical_harness/langchain) - Agent 指南与规范

## 1. 目录定位与职责
`vertical_harness/langchain/` 负责承接底层 LangChain / LangGraph 的执行图流式事件输出、审批中断拦截及生命周期回调分发。
实现将执行图事件流标准化转换为面向宿主和桌面端的统一事件契约。

## 2. 核心类与文件
- `__init__.py`：统一导出流式与回调核心类 `AgentStreaming`、`StreamEvent`、`WorkflowLoggingHandler`。
- `streaming.py` (`AgentStreaming`)：
  - 流式事件迭代器适配器，消费底层图的 `astream` / `astream_events` 输出。
  - 处理文本增量分块、工具调用触发、审批中断（`__interrupt__`）挂起与错误分发。
- `stream_event.py` (`StreamEvent`)：
  - 强类型流事件数据模型，统一承载事件类型、会话标识、运行 ID 及事件载荷。
- `callbacks.py` (`WorkflowLoggingHandler`)：
  - 基于 `BaseCallbackHandler` 实现的安全生命周期日志回调，记录 LLM 调用始末、Token 消耗及工具耗时。

## 3. 关键交互与设计约束
1. **中断状态完整性**：
   - 遭遇人工审批触发的中断时，必须完整保存中断状态并以标准中断事件通知上层，支持后续 Command 恢复。
2. **回调安全规范**：
   - `WorkflowLoggingHandler` 记录执行流程时，绝对禁止输出用户原始 Prompt 文本或未经脱敏的参数对象。
3. **事件契约严格对齐**：
   - 所有向桌面端投递的流事件必须通过 `StreamEvent` 规范化映射，严禁直接泄露底层框架不可控数据结构。
