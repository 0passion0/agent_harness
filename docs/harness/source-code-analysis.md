# 定向开源源码研究

日期：2026-10-20。研究从 [当前审计](current-architecture.md) 的恢复缺口与执行边界出发；不平均审查七个项目，也不将 README 声明当实现证据。以下均为读过的固定版本源码，未安装或执行这些项目。

## 版本、来源与许可证记录

参考 checkout 在 `C:/Users/Hello/.codex/references/agent-harness/`，不加入产品依赖。旧有三个 checkout 本轮复核 HEAD，另四个本轮拉取；不声称任何一个是“最新发布版”。已检查根 LICENSE 和 Git 跟踪的 LICENSE/NOTICE 路径，所研究核心路径未发现独立 NOTICE 要求；仓库其他子目录的第三方许可证仍独立存在，不扩展此结论到全项目。

| 项目 | 固定 Commit | 根许可证 | 研究范围 |
|---|---|---|---|
| Pi | [73f8de80bc87021375f8b525292ac07492253002](https://github.com/earendil-works/pi/tree/73f8de80bc87021375f8b525292ac07492253002) | MIT / `LICENSE` | Loop、工具执行、compaction |
| OpenCode | [d63c8d25f7edf8eb53bf6723e890abe311b91cfa](https://github.com/anomalyco/opencode/tree/d63c8d25f7edf8eb53bf6723e890abe311b91cfa) | MIT / `LICENSE` | compaction 服务、shell 工具结果 |
| OpenHands SDK | [33d8aa29c3078d074799e2fbf5e847aeb2d71360](https://github.com/OpenHands/software-agent-sdk/tree/33d8aa29c3078d074799e2fbf5e847aeb2d71360) | MIT / `LICENSE` | 持久事件、会话重载 |
| Deep Agents | [a2f88434c5a4b5ce5244c468bb9f69b27831842d](https://github.com/langchain-ai/deepagents/tree/a2f88434c5a4b5ce5244c468bb9f69b27831842d) | MIT / 根及 `libs/deepagents/LICENSE` | 图装配、摘要、子代理 |
| mini-SWE-agent | [04d809ceab9df28f9adaed044884180159172930](https://github.com/SWE-agent/mini-swe-agent/tree/04d809ceab9df28f9adaed044884180159172930) | MIT / `LICENSE.md` | 最小 Loop、限额、逐步保存 |
| Goose | [9920efa16d078777a4d9715b2f00d8800a4da188](https://github.com/aaif-goose/goose/tree/9920efa16d078777a4d9715b2f00d8800a4da188) | Apache-2.0 / `LICENSE` | 消息落库、审批恢复 |
| Pydantic AI | [19b1eafbbef19c8891666576c438ae9e0d90ef16](https://github.com/pydantic/pydantic-ai/tree/19b1eafbbef19c8891666576c438ae9e0d90ef16) | MIT / 根及 `pydantic_ai_slim/LICENSE` | 请求/Token/工具预算边界 |

本轮只迁移设计思想，没有复制上游实现。Pi 的 `packages/mcp/LICENSES/`、OpenCode 的 UI/插件/vendor 等另有许可文件，Pydantic 的 CLI 有 `THIRD_PARTY_NOTICES.md`；这些不在本轮复制范围。后续若复制代码，重新核对确切文件及相邻声明，保留适用版权与许可文本。

## Deep Agents：先确认已经拥有的能力

证据：[graph.py `_apply_custom_middleware`](https://github.com/langchain-ai/deepagents/blob/a2f88434c5a4b5ce5244c468bb9f69b27831842d/libs/deepagents/deepagents/graph.py#L211-L246)、[`create_deep_agent`](https://github.com/langchain-ai/deepagents/blob/a2f88434c5a4b5ce5244c468bb9f69b27831842d/libs/deepagents/deepagents/graph.py#L278-L1010)、[摘要实现](https://github.com/langchain-ai/deepagents/blob/a2f88434c5a4b5ce5244c468bb9f69b27831842d/libs/deepagents/deepagents/middleware/summarization.py#L1006-L1130)、[task 状态与结果](https://github.com/langchain-ai/deepagents/blob/a2f88434c5a4b5ce5244c468bb9f69b27831842d/libs/deepagents/deepagents/middleware/subagents.py#L747-L896)。

- 调用链：`create_deep_agent` → 构造默认能力栈 → 按 middleware name 替换 → `create_agent` → LangGraph。相同 name 替换原槽位，不是简单重复追加摘要层。
- compaction 会处理旧工具参数、历史卸载及模型摘要；task 可使用独立 HumanMessage 输入，也有 fork 模式；结果通过 `Command` / ToolMessage 回到父状态。
- 本地 `compose.py` 已在利用这些机制；**不是缺少摘要、子代理或持久化**。本轮对安装版与固定 checkout 的 `graph.py`、`middleware/summarization.py`、`middleware/subagents.py` 做换行统一后的 SHA256 比较，三者一致；checkout 包版本为 0.7.28。
- 权衡：依赖框架中间件顺序和同名替换语义，需要组合回归；原生子代理上下文隔离不能代替 OS 隔离。
- 适配：继续用当前 SummarizationMiddleware / FilesystemMiddleware / SubAgentMiddleware；新增验证和必要宿主策略。不迁移另一套 Loop、消息 reducer、checkpointer。
- 待验证：动态模型路由后实际窗口、编译子代理取消传播、领域摘要保真。Context7 当前文档只用来定位，结论以此固定源码为准。

## Pi：明确 turn 边界与上下文交接

证据：[agentLoop → runLoop](https://github.com/earendil-works/pi/blob/73f8de80bc87021375f8b525292ac07492253002/packages/agent/src/agent-loop.ts#L102-L320)、[工具调度](https://github.com/earendil-works/pi/blob/73f8de80bc87021375f8b525292ac07492253002/packages/agent/src/agent-loop.ts#L508-L522)、[参数校验](https://github.com/earendil-works/pi/blob/73f8de80bc87021375f8b525292ac07492253002/packages/agent/src/agent-loop.ts#L707-L743)、[compaction](https://github.com/earendil-works/pi/blob/73f8de80bc87021375f8b525292ac07492253002/packages/coding-agent/src/core/compaction/compaction.ts#L952-L1055)。

- 调用链：prompt → `runAgentLoop` → `runLoop` → `prepareNextTurn` / `prepareRequest` → `streamAssistantResponse` → `executeToolCalls` → `finishTurn` → 自然结束或继续。错误/取消会结束该轮；输出 length 截断时拒绝执行该响应的工具调用。
- 调度按配置及工具 `executionMode` 选串行/并行；工具调用前有参数校验，生命周期发 start/update/end 事件。这里确认的是执行设计，不保证所有工具可并发。
- `shouldCompact` 在 `contextWindow - reserveTokens` 触发；compaction 结果保留 `firstKeptEntryId`、压缩前 token、摘要用量，并将读过/修改过的文件列表写入摘要及 details。
- 与当前差异：本项目已有原生工具校验/摘要/事件投影；尚无摘要质量基准证明应切换策略。文件证据保留和明确 turn 结束原因值得测试，而非重写 LangGraph。
- 最小适配：在当前多轮回放断言“压缩后仍保留任务约束、关键数值、脚本/交付物路径”；若不通过，先改摘要提示或保留策略。
- 不迁移：Pi TypeScript Loop、消息类型体系、会话存储、steering 队列；它们与 LangGraph 已有能力重叠。

## OpenCode：压缩是有结果的操作，工具成功不是空泛文本

证据：[SessionCompaction 服务](https://github.com/anomalyco/opencode/blob/d63c8d25f7edf8eb53bf6723e890abe311b91cfa/packages/core/src/session/compaction.ts#L208-L253)、[shell 输入与结果](https://github.com/anomalyco/opencode/blob/d63c8d25f7edf8eb53bf6723e890abe311b91cfa/packages/core/src/tool/plugin/shell.ts#L48-L91)、[shell 执行结果](https://github.com/anomalyco/opencode/blob/d63c8d25f7edf8eb53bf6723e890abe311b91cfa/packages/core/src/tool/plugin/shell.ts#L198-L236)。

- `compact(trigger)` 区分 auto/manual/overflow；auto 先判容量，overflow 使用更小的预算，选择 native 或摘要路径，再发布成功/失败结果。刚完成压缩时避免立即再次自动压缩。
- shell 结果显式携带状态、预览、截断、超时等信息，审批后、spawn 前再次检查工作目录；并非简单拼接 stdout 就算成功。
- 权衡：这套实现依赖 Effect 服务、OpenCode session/model/provider 结构；直接移植会引入新运行时。overflow 后自动恢复也不能无差别扩展到写入工具。
- 当前已有 Python 退出码、超时、日志路径与预览；应保留。可借鉴 compaction 成功/失败与用量的可观察性，先证明现有 callbacks 缺了什么。
- 不适合迁移：Effect 容器、session 存储、后台 shell 调度。此次没有完整追踪 OpenCode 用户入口或全量 Loop，因此矩阵中未确认的能力仍标未知。

## OpenHands SDK：持久事件支撑重载

证据：[LocalConversation 默认事件回调](https://github.com/OpenHands/software-agent-sdk/blob/33d8aa29c3078d074799e2fbf5e847aeb2d71360/openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py#L441-L448)、[EventLog append](https://github.com/OpenHands/software-agent-sdk/blob/33d8aa29c3078d074799e2fbf5e847aeb2d71360/openhands-sdk/openhands/sdk/conversation/event_store.py#L188-L238)、[ConversationState 重载](https://github.com/OpenHands/software-agent-sdk/blob/33d8aa29c3078d074799e2fbf5e847aeb2d71360/openhands-sdk/openhands/sdk/conversation/state.py#L509-L567)、[服务加载](https://github.com/OpenHands/software-agent-sdk/blob/33d8aa29c3078d074799e2fbf5e847aeb2d71360/openhands-agent-server/openhands/agent_server/conversation_service.py#L1031-L1085)。

- 运行事件经 callback 追加到 EventLog；append 先写事件 JSON，再更新内存索引和长度标记。重载读取 base state、校验 conversation id、连接 EventLog 并 rebuild view，保留统计状态。
- 代价：需保证事件存储和视图重建契约；“记录落盘”不自动证明断电原子性，更不保证工具副作用恰好一次。
- 当前差异：LangGraph 已有可恢复图状态，缺口在 Electron 运行记录与 backend.exited 结算。无需复制 OpenHands 的完整事件账本来修该问题。
- 最小适配：先让宿主运行元数据足够重建失败记录，并使后台退出结算不依赖已死亡的 RPC；继续把图状态放在现有 Checkpointer。
- 不迁移：ConversationState 体系、第二份对话事件源、agent server。容器/远程 workspace 安全机制本轮未审计。

## Goose：持久消息与定向恢复，避免泛化“可恢复”

证据：[state machine 消息副作用](https://github.com/aaif-goose/goose/blob/9920efa16d078777a4d9715b2f00d8800a4da188/crates/goose/src/agents/state_machine/session.rs#L40-L75)、[SQLite transaction](https://github.com/aaif-goose/goose/blob/9920efa16d078777a4d9715b2f00d8800a4da188/crates/goose/src/session/session_manager.rs#L2104-L2147)、[确认恢复入口](https://github.com/aaif-goose/goose/blob/9920efa16d078777a4d9715b2f00d8800a4da188/crates/goose/src/agents/agent.rs#L694-L762)、[ACP 加载](https://github.com/aaif-goose/goose/blob/9920efa16d078777a4d9715b2f00d8800a4da188/crates/goose/src/acp/server/load_session.rs#L381-L436)。

- 消息副作用在 SQLite 事务内追加；ACP 加载会话、向客户端重放历史并发现 pending confirmations。
- `resume_state_machine_turn_inner` 只在待确认工具或已持久化但未应用的确认响应存在时恢复；否则返回 None。此证据不能支持“任意运行中的工具崩溃后自动续跑”。
- 与当前相似：审批恢复已有；启发在于明确区分 transcript reload、pending approval resume、未知副作用后的重新执行。
- 最小适配：恢复 UI 明示这三类状态；当前先修丢失的 run 终态。保留 LangGraph 的 resume。
- 不迁移：Rust 状态机与 ACP 服务；全量模型错误处理和扩展权限本轮未知。

## mini-SWE-agent：预算检查和轨迹保存可以很小

证据：[DefaultAgent run/query/execute_actions](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/default.py#L88-L157)、[serialize/save](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/default.py#L159-L190)。

- `run → step → query → env.execute → observations`；调用前检查 step/cost/wall time；每一步 finally 保存轨迹，格式错误单独计费并限制连续次数。
- 权衡：cost 检查基于已发生用量，不能严格阻止最后一次响应越界；finally save 也不代表可恢复半完成的工具执行。`env.execute` 的安全性取决于具体 Environment，本轮没有全量检查。
- 当前并不缺 Loop；可借鉴小而确定的基线和每轮轨迹。本项目多轮脚本已保存前序轮次，不重复建立新 runner。
- 适配：用确定性 fake model/tool 构造故障和预算边界案例，接入现有评测。生产固定调用上限与当前测试契约冲突，不直接照搬默认值。

## Pydantic AI：分清请求前限额与响应后核算

证据：[UsageLimits](https://github.com/pydantic/pydantic-ai/blob/19b1eafbbef19c8891666576c438ae9e0d90ef16/pydantic_ai_slim/pydantic_ai/usage.py#L492-L654)、[模型请求前处理](https://github.com/pydantic/pydantic-ai/blob/19b1eafbbef19c8891666576c438ae9e0d90ef16/pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2000-L2171)、[响应后核算](https://github.com/pydantic/pydantic-ai/blob/19b1eafbbef19c8891666576c438ae9e0d90ef16/pydantic_ai_slim/pydantic_ai/_agent_graph.py#L2300-L2306)。

- 请求前检查 request/已累计 token/cost；可选择额外 count_tokens 请求预估输入；响应后检查新累计用量；工具前对 projected tool calls 检查。
- 这些是不同保证：已报用量的停止阈值、请求输入容量、实际花费上限不能互换；流式响应和并发预留还需另外设计。
- 本项目 callbacks 已测量用量，桌面已有上下文容量门禁；评测 ConversationBudget 有跨轮约束，但产品运行没有统一累计费用封顶。这是需求相关差异，不是已证实 bug。
- 最小适配：先在 benchmark 中统一记录 root/summary/subagent 的请求与完整性，再讨论可选预算；复用现有中间件/回调，不引入 Pydantic AI runtime。
- 不适合迁移：另一个 Agent graph、模型/工具抽象及 durable runtime。没有研究其全部 durable-exec 后端，不能据此比较全面恢复能力。
