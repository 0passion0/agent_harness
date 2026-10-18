# 后端 Streaming 接口

`vertical_harness.astream_agent()` 将一次 Agent 执行转换为 `StreamEvent` 异步迭代器。
执行事件与传输分离：宿主进程可以把事件交给前端，也可以放进 JSON-RPC notification 的 `params`。
`astream_agent` 与 `ainvoke_agent` 使用配置的整次调用 deadline，可传 `timeout_seconds` 覆盖；超时发出 run.failed 并传播 TimeoutError。直接调用原生图时由宿主自行管理总 deadline，详见[稳定化边界](STABILIZATION.md)。
这里不实现 CLI、前端、JSON-RPC server 或进程通信协议，也不新增依赖。

```python
from contextlib import aclosing
from uuid import uuid4

from vertical_harness import HarnessSettings, RunContext, aopen_agent, astream_agent


async def run(prompt, thread_id, send_notification):
    config = {"run_id": uuid4(), "configurable": {"thread_id": thread_id}}
    async with (
        aopen_agent(HarnessSettings()) as agent,
        aclosing(astream_agent(
            agent,
            {"messages": [{"role": "user", "content": prompt}]},
            config=config,
            context=RunContext(tenant_id="demo", user_id="demo"),
        )) as events,
    ):
        async for event in events:
            await send_notification("agent.event", event.model_dump(mode="json"))
```

`send_notification` 由未来的宿主传输层提供。租户和用户身份由宿主从可信会话获取。
宿主可在自己的生命周期内复用打开的 Agent；同一持久化线程的运行与恢复应串行执行。
现有 callbacks、checkpoint 配置和 `context` 原样传入框架，不需要日志解析或第二次模型调用。
底层使用 [LangChain 原生 astream_events v2](https://reference.langchain.com/python/langchain-core/runnables/base/Runnable/astream_events)。

## 事件契约

每条事件包含 `type`、`run_id`、`thread_id`、从 1 递增的 `seq`、`operation_id`、
按根到直接父调用排序的 `parent_ids`、`name`、`agent_name`、`node` 和 `data`。
`seq` 只表示本次执行的交付顺序，不表示可恢复消费游标；并行调用通过各自的 `operation_id` 区分。

| type | 含义 / data |
| --- | --- |
| `run.started` | 本次执行开始 |
| `model.started` | 一次模型调用开始 |
| `model.delta` | `text` 文本增量及 `message_id`；按 `operation_id` 聚合 |
| `model.completed` | 本次模型调用的完整 `text` 及 `message_id`，用于校正增量或非流式模型回退；模型上报时附带 `usage` 用量计数 |
| `tool.started` | 工具开始；委派工具额外提供 `subagent_type` |
| `tool.completed` / `tool.failed` | 工具完成 / 失败；异常只提供 `error_type`，可恢复错误不会把整次执行标为失败 |
| `subagent.started` / `subagent.completed` | 本地子智能体开始 / 完成；`agent_name` 标识子智能体 |
| `chain.started` / `chain.completed` | 图节点、工作流等子调用的执行进度 |
| `custom.progress` | 插件主动发布的 JSON 进度，`name` 为自定义事件名，`data.value` 为载荷 |
| `run.completed` | 整次执行完成，`output` 为最后一条消息的文本 |
| `run.interrupted` | 等待审批，`interrupts` 包含每项的 `id` 和 JSON `value` |
| `run.failed` | 整次执行失败，只有 `error_type`，随后重新抛出原异常 |

`model.completed.text` 和 `run.completed.output` 是完整文本，不能继续追加到增量末尾。
用量只在 `model.completed.usage` 提供；不要累加增量 chunk 的计数。未提供 `usage` 表示模型未上报，不代表零消耗。
模型文本仅包含文本内容块，内部 state、模型配置、工具参数/结果、reasoning 内容块不会自动输出。
自定义进度与审批载荷是插件明确公开的数据，插件应只放前端需要的 JSON 值。
插件可调用 `adispatch_custom_event("progress", {"step": "checked"})` 发布进度。

## 恢复、取消与边界

- 挂起后以同一 `thread_id`、新的 `run_id` 调用 `astream_agent(agent, Command(resume=...), ...)` 恢复。
  按 interrupt ID 决策时传 `Command(resume={interrupt_id: decision})`。历史重放可在 config 的 configurable 中指定 checkpoint。
- 取消运行由宿主取消消费任务，`CancelledError` 原样传播；提前停止消费必须使用 `aclosing`。
  流关闭会取消底层执行；外层 `aopen_agent` 随后释放其管理的 MCP 和存储资源。
  取消确认由宿主在清理完成后发送，不依赖已关闭的事件流再产生事件。
- 失败或挂起是整次运行的终态；前端应同时结束仍显示运行中的子调用。
  收到 `run.failed` 后宿主仍需处理重新抛出的异常，不要再发送第二条失败通知。
- 本地嵌套图的事件会传播。远端 `AsyncSubAgent` 的内部 token 与节点事件需要远端另行提供事件通道；本接口只能看到宿主收到的委派和监控工具事件。
- 当前不提供断线重放、持久化事件队列或背压队列。SDK 在内部缓冲事件，宿主应及时消费；需要慢客户端隔离时在传输层加入有界队列与策略。

`tests/test_streaming.py` 使用真实 Deep Agents / LangGraph 图和本地假模型，覆盖文本分块、
工具成功与错误、自定义进度、嵌套 Subagent、审批恢复、异常、取消和提前关闭，无需真实 LLM 凭据。
