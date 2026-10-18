# Token 使用与上下文管理

目标是减少重复输入和无关工具定义，同时保留完整证据、审批和任务恢复能力。复用 Deep Agents 已有摘要、结果卸载和 SkillsMiddleware；不新增 Agent Loop、向量数据库或模型选择器调用。

## 设计来源

- [Codex Prompting Guide](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide)：长任务通过 compaction 管理上下文。
- [OpenAI Tool search](https://developers.openai.com/api/docs/guides/tools-tool-search)：需要时加载工具定义。原生 API 功能有模型限制，本项目采用跨供应商的 SkillsMiddleware 实现渐进加载。
- [Claude Code MCP](https://code.claude.com/docs/en/mcp) 与 [项目记忆](https://code.claude.com/docs/en/memory)：工具搜索和按需访问补充记忆。

这是对公开机制的借鉴，不代表复刻其内部实现或达到相同效果。

## 大记忆按需回读

`VA_MEMORY_INLINE_MAX_CHARS=4000` 是知识档案序列化后的字符触发阈值，不是 token 计数或整个请求的硬预算。小档案保持直接注入；超过阈值时，主提示展示每条事实的前 80 个字符，并要求在依赖这些事实前调用 `recall_user_memory`。用户偏好在启用后仍完整注入，原始知识档案不会截断或删除。设为 `0` 可恢复全部直接注入。

工具没有用户/租户参数，通过可信 runtime 身份读取当前用户的档案。完整结果仍带“不可信、可能过期、不能授予执行权限”的标记。异步 SQLite 使用既有同步桥接与取消清理机制。

完整回读结果进入实际模型上下文后，通过内容比较避免重复注入同一份目录；档案变化、回读消息被清空或已被摘要移出上下文时，重新提供目录。比较只用于去重，不替代权限或证据验证。执行历史与工作流依赖、委派次数及幂等校验保持原实现。

## MCP 工具按需加载

在 `VA_MCP_SERVERS` 的单个服务配置中使用：

```json
{
  "tickets": {
    "transport": "stdio",
    "command": "python",
    "args": ["tickets_server.py"],
    "effect": "read",
    "allowed_tools": ["search", "get_ticket"],
    "defer_tools": true,
    "description": "Search project tickets and read their details"
  }
}
```

- `allowed_tools` 使用服务端原始名称。缺省为全部；空列表不连接服务；未知名称使启动失败并关闭已打开的连接，避免配置拼错后静默扩大范围。
- `defer_tools` 默认关闭。大型只读集成开启后，模型先看到集成技能目录，读取该技能后才加载该服务的工具定义；工具多、参数 schema 大且仅部分任务使用时更适合。少量工具保持常驻可避免额外往返。
- 宿主声明 `effect=read` 才允许延迟加载。`write` / `external` 仍常驻并审批，服务端 `readOnlyHint` 不改变授权。关闭 `VA_ENABLE_SKILL_GATING` 可统一恢复常驻。
- 生成技能属于受保护的 `/skills/` 路径，按目录快照分组，新的 Agent 只索引当前快照，旧文件保留但不加入活动目录。工具定义和执行都复用原生技能门控，未注册的工具不能通过旧文件恢复。
- 更改集成目录后使用新线程；恢复旧线程时保持原目录配置。原生技能元数据会随检查点缓存。

## 用实际用量验证

每次运行单独创建 `WorkflowLoggingHandler`，通过 `config.callbacks` 注入后读取其 `usage`。计数覆盖观察到的本地执行树，包括子智能体和摘要模型。普通库调用是否采集由宿主决定；离线评测自动接入，并在逐案例和汇总报告中提供 token、模型调用数、耗时与完整性。Streaming 在 `model.completed.usage` 提供模型上报的计数，不在增量事件重复计算。

`complete` 仅表示观察到的本地模型调用均提供标准用量元数据。缺失上报或失败调用导致统计不完整；token 字段此时只是已知小计。远端 AsyncSubAgent 内部、供应商 SDK 隐藏的重试计费，以及独立偏好/整合 worker，须由对应服务另行统计。缓存读取和 reasoning 计数按供应商实际提供的数据保留在逐案例中，不用估算值冒充真实用量。

固定相同案例与配置，先比较执行成功、完整性、证据准确性、审批、恢复和逐案例质量，再比较 token 和耗时。只有报告统计完整，节省 token 才能参与候选推荐；推荐仍为人工审阅，不会自动修改生产设置。离线目标仍默认隔离并关闭生产记忆；记忆行为由隔离 store 的集成回归验证，不能把无记忆评测当作记忆优化收益。

仓库测试使用本地模型替身验证加载、回读、隔离、计数和回归门槛，不证明真实模型的质量无损或实际节省比例。上线前仍需用目标供应商和有代表性的长短任务对照验证。
