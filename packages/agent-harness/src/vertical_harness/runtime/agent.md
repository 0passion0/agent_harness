# 资源、运行与会话

AgentRuntime 拥有配置周期的 Checkpointer、Store 和 Shell。RunManager 维护单个 ActiveRun，暂停状态留在框架检查点；SessionService 管快照和用量。

- 不导入 bridge 或 desktop；宿主负责原始输入转换与通知回调注入。
- 资源全部准备成功才发布配置；借用方不能关闭配置周期资源。
- 发出终态前关闭本次运行流并清理任务；取消必须传播。
- 会话删除先等待任务，再关后台作业，最后删除数据。
- 恢复校验运行、中断、租户和用户；不得自行重放已完成工具副作用。
