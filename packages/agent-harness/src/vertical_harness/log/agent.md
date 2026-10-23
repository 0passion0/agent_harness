# 日志与追踪

HarnessLogging 配置日志；TracingSession 管理单次调用的追踪上下文。

只记录允许的结构化元数据，不写用户原文、密钥或任意输入对象。stdout 保留给 CLI 结果或桌面协议；不吞掉业务异常和取消。
