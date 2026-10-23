# 原生持久化

CheckpointFactory 和 StoreFactory 管理框架原生 Memory/SQLite 资源，不依赖文件工具或桌面桥接。

- 使用上下文管理器打开和关闭；AgentRuntime 持有配置周期资源。
- 不改变框架存储格式，不在代码整理时删除用户数据库。
- 会话删除由宿主先取消运行、结束会话作业，再调用持久化删除。
