# 框架事件与回调

AgentStreaming 投影允许的流事件并关闭底层事件流；StreamEvent 定义线上字段；ToolDetails 收集单次运行详情；WorkflowLoggingHandler 记录安全元数据。

- 不管理宿主生命周期或执行业务工具，不导入 runtime/desktop。
- 开放框架载荷在适配边界收敛类型；终态、取消和异常保留语义。
- 日志不记录用户内容、密钥、完整参数和工具输出；面向用户的事件保留既有字段。
