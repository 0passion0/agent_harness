# Deep Agents 垂直领域插件化 Harness（参考架构）

本仓库采用两个**独立 Python distribution**：通用 `vertical-agent-harness` 和演示插件 `vertical-plugin-research-citation`。

核心默认不启用领域插件；`.env.example` 显式启用科研样例。升级前请阅读[稳定化与迁移说明](docs/STABILIZATION.md)：默认数据目录、文件记忆 namespace 与持久化资源托管有调整，已有数据不会自动移动或删除。

统一模型入口将 SDK 的 `max_retries` 设为 5，覆盖主模型、动态路由、记忆提取和评估模型。DeepSeek/OpenAI 兼容请求遇到连接异常、超时、408/409/429 或 5xx 时，首次失败后最多重试 5 次（共 6 次请求）；指数退避基准为 0.5、1、2、4、8 秒，加入随机抖动，并遵循 SDK 的服务端 Retry-After 规则。鉴权/参数错误默认不重试，取消与既有总 deadline 保持生效。其他提供商的错误分类和退避由各自 SDK 决定。流式请求在返回数据前同样重试，已开始输出后发生中断则向上传播错误，避免自动重放造成重复文本或工具调用；此策略不会重试整个工作流或有副作用的工具。

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ./packages/agent-harness -e ./packages/research-citation
cp .env.example .env  # 在 .env 中配置 VA_API_KEY 与 VA_MODEL（如 deepseek:deepseek-flash）
vertical-agent --list-plugins
vertical-agent '检查下面正文的参考文献索引：正文 [1] 和 [3]；参考文献依次为 A、B。'
pytest -q
```

也可使用锁定的 uv workspace：`uv sync --frozen --all-packages --extra dev` 后 `uv run --package vertical-agent-harness vertical-agent --list-plugins`。

## 图片理解

使用 `--image` 将本地图片直接附加到用户消息，可重复传入多张，也可只传图片：

```bash
vertical-agent '解释这张图里的内容' --image ./chart.png
vertical-agent '比较两张截图的差异' --image ./before.png --image ./after.png
```

支持 PNG、JPEG、GIF、WebP，单张非空且不超过 20 MiB；按扩展名选择 MIME，实际图片解码由模型服务验证。图片路径相对当前命令行目录，支持 `~`，无需先放入 Agent 工作区。同步、MCP 异步调用和 Time Travel / Fork 使用相同的图片消息；库调用 `invoke` / `ainvoke` / `astream_agent` 也可直接传 LangChain 标准图片内容块：

```python
inputs = {"messages": [{"role": "user", "content": [
    {"type": "text", "text": "解释这张图片"},
    {"type": "image", "url": "https://example.com/chart.png"},
]}]}
```

所选模型必须支持视觉输入；当前默认的 `deepseek-flash` 支持，参见 [DeepSeek 图像理解文档](https://api-docs.deepseek.com/zh-cn/guides/vision/)。启用动态路由时须确认实际选中的模型也支持图片。当前 `langchain-deepseek` 会将工具返回的内容数组序列化为 JSON 文本，因此 DeepSeek 图片理解请使用 `--image` 或用户消息图片块，不能依赖 `read_file` 的图片工具结果。图片会发送给配置的模型服务，并保存在对话检查点中；启用 LangSmith tracing 时也可能随输入上传。

## 记忆、用户偏好与每周后台整合

已有 LangGraph 检查点保存同一线程的短期状态，SQLite Store 和 `/memories/` 文件路由保存跨线程记忆。本次基于 [LangMem](https://langchain-ai.github.io/langmem/reference/memory/) 补充结构化用户偏好、自动召回与 Background Memory Consolidation。自动处理默认关闭：

```dotenv
VA_ENABLE_MEMORY=true
VA_ENABLE_USER_PREFERENCES=true
VA_ENABLE_MEMORY_CONSOLIDATION=true
VA_MEMORY_CONSOLIDATION_INTERVAL_DAYS=7
# 可选：用更经济的、支持工具调用的模型完成提取和整合。
# VA_MEMORY_MODEL=deepseek:deepseek-flash
```

| 配置 | 默认值 | 行为 |
|---|---|---|
| `VA_ENABLE_MEMORY` | `true` | 长期记忆总开关；关闭时不挂载记忆路由、不加载记忆、不采集或学习；不删除已有数据，检查点仍独立工作 |
| `VA_ENABLE_USER_PREFERENCES` | `false` | 从用户明确表达中提取稳定偏好，在本次回答前更新并跨线程召回；这是自动偏好开关，手写记忆文件仍由长期记忆开关控制 |
| `VA_ENABLE_MEMORY_CONSOLIDATION` | `false` | 将用户输入加入待处理队列，供独立后台进程按周整合 |
| `VA_MEMORY_CONSOLIDATION_INTERVAL_DAYS` | `7` | 每个用户首次采集后等待的天数，以及完成一次整合后的间隔，范围 1–365 |
| `VA_MEMORY_MODEL` | 主模型 | 偏好和整合模型，复用 `VA_API_KEY` / `VA_BASE_URL` |

独立启动后台 worker；它与对话进程读取相同的配置及 SQLite 文件，不连接 MCP、不运行领域插件：

```bash
vertical-agent --memory-worker
```

进程每分钟检查持久化到期时间，**实际整合默认每 7 天一次**，没有新内容时不调用模型。应由服务管理器保持 worker 运行；仅设置开关不会启动后台进程。也可让系统计划任务定期执行 `vertical-agent --consolidate-memory`，它只处理已经到期的用户，失败返回非零退出码；例如每小时检查一次，整合周期仍为每周。后台进程未运行期间不会执行任务，重启后补处理已到期队列。

自动记忆使用可信 `RunContext.tenant_id` + `user_id` 的 SHA-256 命名空间，不接受模型提供身份；同一用户换 `thread_id` 仍可召回。调用方必须为不同租户/用户分配不同的检查点线程，`thread_id` 不是身份鉴权。偏好最多 20 条、每条 300 字符；长期事实最多 30 条、每条 500 字符，召回不增加模型调用或 embedding 成本。LangMem 的结构化补丁更新已有单一档案，保留有效条目、去重，并以用户的新明确更正替换旧内容。新自动记忆不迁移已有 `/memories/` 文件。

整合仅采集用户文本；模型回答和工具文档不会自动成为长期事实。输入按 8,000 字符分片、每批最多 50 片和 32,000 字符，按采集顺序处理；每个成功批次通过 Store 的 `batch` 同时保存摘要和消费队列。模型失败保留输入和到期时间，下次继续未完成批次；偏好提取失败记录日志并在后续调用重试，不阻断正常回答。处理成功后只保留摘要及哈希去重回执，队列原文删除；尚未处理的用户原文会保存在数据库中，并在处理时发送给配置的模型。记忆是可过期的参考资料，不能覆盖当前用户请求、证据或授予工具执行权限。

部署约束：独立 worker 需要共享的持久化 SQLite，拒绝 `memory` / `:memory:`；同一数据库运行一个 worker，同一用户的偏好更新由宿主串行调用。多副本并发写入需要另行加入分布式租约或事务冲突控制。长期 Store 不随 Time Travel 回滚；重放相同消息通过哈希回执去重。

作为库使用可通过 `vertical_harness.engine.memory.consolidate_memories(settings, store)` 执行一次到期检查；同步 `build_agent` 与异步 `aopen_agent` 均自动接入启用的偏好和召回，无需改调用载荷。

## MCP 服务

支持 stdio 子进程和 Streamable HTTP，通过 `.env` / `VA_MCP_SERVERS` 配置服务名到连接参数的 JSON 对象：

```dotenv
VA_MCP_SERVERS='{"local":{"transport":"stdio","command":"python","args":["/absolute/path/server.py"],"cwd":"/absolute/path","env":{"API_KEY":"replace_me"}},"remote":{"transport":"streamable_http","url":"http://localhost:8000/mcp","headers":{"Authorization":"Bearer replace_me"}}}'
```

`http` 是 `streamable_http` 的别名，不支持旧 SSE 传输。stdio 的 `command` 与 `args` 直接传给子进程，不经过 shell；所需凭据通过 `env` 显式传递。HTTP 服务须提前启动，支持 `headers` 请求头。每个服务可设置 `timeout`（默认 30 秒，作用于初始化和工具请求）及 `effect`（默认 `external`）。仅对宿主确认只读的服务设置 `"effect":"read"` 以免审批；不信任服务器提供的只读提示作为授权。

CLI 正常执行与 Time Travel / Fork 会自动走异步路径，整次调用持有 MCP 会话，结束、异常或取消时关闭连接与子进程。工具以 `<服务名>_<工具名>` 注册，重名报错，默认通过现有 HITL 执行 `approve/reject` 审批。审批恢复仍由宿主审批 UI 使用 `Command(resume=...)` 完成。`VA_SUPERVISOR_ONLY=true` 时不连接 MCP，也不向主智能体注册 MCP 工具；列表与历史查询不连接 MCP。

支持 MCP Tool Annotations：`title`、`readOnlyHint`、`destructiveHint`、`idempotentHint`、`openWorldHint`。原生适配器将注解保留在 `tool.metadata["mcp"]["tool"]["annotations"]`，Python 字段名使用 snake_case（如 `read_only_hint`）；`ToolContribution.mcp_annotations` 可直接读取，未提供时返回 `None`，未声明的提示保持缺省，不补造值。审批请求的 `description` 展示服务端注解并标明它们是不可信提示，调用名称与参数仍分别位于 `name` / `args`。注解不覆盖宿主 `effect`，不自动免审、重试或去重；`readOnlyHint=true` 仍不能跳过 `external` / `write` 审批。有关提示的语义与信任边界，参见 [MCP 规范](https://modelcontextprotocol.io/specification/2025-06-18/schema#toolannotations)。

作为库使用时，以 `aopen_agent` 管理连接及缺省异步存储，并在上下文内调用 `ainvoke` / `astream`：

```python
from vertical_harness import HarnessSettings, RunContext, aopen_agent

async def run():
    async with aopen_agent(HarnessSettings()) as agent:
        return await agent.ainvoke(
            {"messages": [{"role": "user", "content": "使用已配置的 MCP 工具查询"}]},
            config={"configurable": {"thread_id": "mcp-demo"}},
            context=RunContext(tenant_id="demo", user_id="demo"),
        )
```

同步持久化调用使用 `with open_agent(settings) as agent`；`build_agent` 仅在纯内存或显式传入托管 store 时直接使用。MCP 使用 `aopen_agent`。调用方提供的 store/checkpointer 仍由调用方关闭。插件 open/aopen 生命周期与配置见[插件契约](docs/STABILIZATION.md)。MCP 适配使用 LangChain 原生 `MCPAdapter`（beta），API 边界参见[官方文档](https://docs.langchain.com/oss/python/langchain/mcp)。

## 日志

CLI 启动时统一配置 Python 标准库 `logging`，默认输出 INFO 级别文本到 stderr；stdout 保留插件 JSON 或运行结果。支持 UTF-8 滚动文件，所有配置均从 `HarnessSettings` / `VA_*` 环境变量及 `.env` 加载：

| 配置 | 默认值 | 用途 |
|---|---|---|
| `VA_LOG_LEVEL` | `INFO` | `DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL` |
| `VA_LOG_FORMAT` | `text` | 文本键值日志或单行 `json` |
| `VA_LOG_FILE` | 不设置 | 可选日志文件路径，自动创建父目录 |
| `VA_LOG_MAX_BYTES` | `10485760` | 单文件轮转阈值，必须大于 0 |
| `VA_LOG_BACKUP_COUNT` | `5` | 备份文件数量，必须大于 0 |

需要全部流程日志时，在 `.env` 中设置：

```dotenv
VA_LOG_LEVEL=DEBUG
VA_LOG_FORMAT=json
VA_LOG_FILE=./.agent-workspace.runtime/logs/harness.log
```

DEBUG 覆盖插件发现和契约验证、工具/技能/子智能体注册、资源加载、存储生命周期、模型初始化与路由、中间件配置和 Agent 编译。运行时通过 LangChain 原生 callbacks 记录完整执行树中的链、模型、工具及嵌套子调用的开始、完成、失败、取消、挂起和框架重试；同步、异步共用同一机制。`run_id` 关联一次 CLI 调用，`operation_id` / `parent_operation_id` 关联执行树，结束事件包含 `duration_ms`。

插件使用 `logging.getLogger(__name__)` 即可接入，无需在宿主日志模块登记插件或智能体名称。应用启动配置 root logger，保留宿主已有 handler；导入库不会配置 root logger。重复初始化只替换本模块创建的 handler。

结构化字段通过 `extra={"event_fields": {"operation": "scan", "count": 3}}` 显式提供，值使用 JSON 可序列化的安全元数据。基础设施不枚举领域字段，插件可以定义自己的事件；保留字段如 `timestamp`、`level`、`run_id` 不能被事件字段覆盖。

本项目事件只记录阶段、标识、数量、状态和耗时，不记录 prompt、正文、文献内容、模型响应或凭据。异常保留类型与调用位置，不输出异常消息和局部变量。DEBUG 同时遵循第三方库自身的日志策略，第三方消息和宿主自有 handler 不属于本项目事件的脱敏边界。逐 token 内容仍使用 LangSmith 等专用观测机制。

作为库集成时，由宿主决定是否配置 logging，并在每次执行时注入独立 callback：

```python
from uuid import uuid4

from vertical_harness import WorkflowLoggingHandler, configure_logging, log_context

configure_logging(settings)  # 进程启动时调用一次；也可沿用宿主的 logging 配置
with log_context(str(uuid4())):
    result = agent.invoke(
        inputs,
        config={
            "configurable": {"thread_id": thread_id},
            "callbacks": [WorkflowLoggingHandler()],
        },
        context=context,
    )
```

异步调用使用相同配置传给 `agent.ainvoke()`。滚动文件适用于单进程；多个进程应输出 stderr，由部署环境统一收集与轮转。

## 插件零改主流程（Supervisor + LangGraph Workflow Plugins）

插件必须在自己的 `pyproject.toml` 声明：

```toml
[project.entry-points."vertical_agent.plugins"]
"research.citations" = "research_citation.plugin:ResearchCitationPlugin"
```

主包仅通过 `importlib.metadata.entry_points` 扫描显式启用的插件。每个工作流插件通过 `WorkflowContribution` 原生接入 Deep Agents 的 `CompiledSubAgent`（单进程预编译图）或 `AsyncSubAgent`（Agent Protocol `graph_id`），并强制声明三层隔离的数据模式：

- `InternalState` (`internal_state`)：定义工作流内部节点使用的数据（外部调用方或 LLM 不可直接写入，亦不对外泄露）；
- `input_schema`：定义调用方/主智能体需要提供的数据（兼容 `messages` 协议）；
- `output_schema`：定义工作流最终返回给主智能体的数据（包含 `messages` 与 `structured_response`）。

宿主 `SupervisorPolicyMiddleware` 提供确定性监督治理（工作流白名单、租户 RBAC、`depends_on` 依赖顺序、`max_dispatches` 重试上限、`idempotent` 幂等复用、`result_schema` 验收与 `InterruptOnConfig(when=...)` 按需审批）。

依赖、幂等与结果校验当前面向 compiled 工作流；async 的这些组合会明确拒绝。纯监督按 agent 隔离并在执行层拒绝直接文件操作。同批重复幂等委派只执行第一个，其余待结果后重试。

## Time Travel 与 Time Travel Fork（`durability="sync"`）

默认启用 `durability="sync"`，SQLite 检查点位于工作区旁 `<workspace-name>.runtime/checkpoints.sqlite`，可配置 `VA_RUNTIME_DATA_DIR` / `VA_CHECKPOINT_DB_PATH`。跨线程 fork 复制根 checkpoint 的 pending writes，拒绝已有目标及可识别嵌套子图。指定 checkpoint ID 是回放，可能重跑后续步骤；不指定 ID 才是正常恢复。

```bash
# 列出已注册的插件工作流（含模式、三层状态与监督策略）
vertical-agent --list-workflows

# 查看指定线程的检查点历史
vertical-agent --thread-id demo-thread --history

# 从指定历史检查点回放或注入新状态分叉到新线程（Time Travel Fork）
vertical-agent --thread-id demo-thread \
  --checkpoint-id <checkpoint_id> \
  --fork-thread-id demo-thread-fork \
  --update-state '{"messages": [{"role": "user", "content": "使用修正后的参考文献重新核查"}]}'
```

## 核心技术选择

- Agent 运行：`deepagents.create_deep_agent()` + `CompiledSubAgent` / `AsyncSubAgent` 工作流委派；无自制 Agent Loop。
- Skills：`SKILL.md` 及 `SkillsMiddleware`；演示插件同时提供确定性 LangGraph 工作流 `citation_audit` 与 `metadata.include_tools` 技能工具。
- 工作空间：开发使用 `FilesystemBackend(virtual_mode=True)`；并非安全 OS sandbox。
- Durability：默认 `durability="sync"` 配合 SQLite Checkpointer（`SqliteSaver` / `AsyncSqliteSaver`），支持跨进程恢复、Time Travel 回放与 Time Travel Fork；生产环境可替换为 Postgres Checkpointer。
- 评测：LangSmith 官方 Trace/Dataset/Evaluator；业务规则继续用确定性 Pydantic/pytest 检查。
- 权限：高风险工具与 `WRITE`/`EXTERNAL` 工作流必须 `approve/reject`，真正越权保护仍须后端逐次执行 RBAC/ABAC。插件本身是受信任的 Python 代码。

## 边界与不足

1. 此仓库是**参考实现**而非已投产系统；当前没有提供生产数据库迁移、队列、认证、审计服务、外部文件解析或 Web API。MCP 目前接入工具发现与调用，未提供 OAuth 登录流程、资源/提示词导入或连接池。
2. 本地开发默认使用 SQLite Checkpointer 存储检查点，多实例高并发生产部署需切换至 Postgres Checkpointer。
3. 默认 `context_schema` 使用 Python dataclass 以匹配 LangGraph runtime，外层环境配置与插件契约使用 Pydantic v2。
4. 示例引文工作流/工具只检查数字引用与文末文献表的索引对应关系，不做真实性或证据支持验证。
5. 安装与完整 Agent 运行需相应官方包及 LLM 凭据；框架 API 可能演进，部署时应锁定并测试依赖版本。

详细设计参见 `docs/ARCHITECTURE.md`。

## Token 使用优化

大知识档案默认通过简短目录和 `recall_user_memory` 按需回读；小档案与启用后的用户偏好保持直接注入。MCP 可用 `allowed_tools` 限定工具，并为大型只读集成设置 `defer_tools=true`，复用原生 SkillsMiddleware 延迟加载工具定义。写操作与外部操作继续使用现有审批流程。

离线评测报告增加模型调用数、实际输入/输出 token、耗时与用量完整性；在逐案例质量无退步且统计完整时，减少 token 的候选可以进入人工审阅。配置、统计边界和设计来源见 [Token 优化说明](docs/TOKEN_EFFICIENCY.md)。

前端实时集成可直接消费 `vertical_harness.astream_agent()` 的模型增量、工具状态和 Subagent 进度事件。
接口与传输分离，可由后续本地进程通过 JSON-RPC notifications 转发；契约与调用示例见 [Streaming 接口](docs/STREAMING.md)。

## LangSmith Tracing、评测与反馈回归

框架原先依赖 SDK 隐式 tracing；现在通过 `observability.tracing_session` 显式读取 `.env` 中的 `LANGSMITH_*`，CLI 和 `aopen_agent` 管理上传及连接生命周期。示例默认关闭；将 `LANGSMITH_TRACING=true` 并设置有效密钥后，普通对话会上传输入、输出和工具轨迹。同步库调用用 `with tracing_session(): agent.invoke(...)`。`--run-id <UUID>` 与日志中的 run ID 对应，可用于反馈关联；Time Travel/Fork 同样支持。

独立入口 `vertical-eval` 提供主智能体/插件离线评测、LangMem 候选提示词、在线抽样评测、用户反馈与 Regression Dataset。模型统一使用 `VA_MODEL`、`VA_EVAL_MODEL`、`VA_OPTIMIZER_MODEL`，共用 `VA_API_KEY` / `VA_BASE_URL`；未指定评审模型时回退到 `VA_MODEL`，优化模型依次回退到评审模型、主模型。

完整配置、样本格式、运行命令和模块边界见 [评测使用说明](docs/EVALUATION.md)。可直接执行的无模型示例：

```bash
vertical-eval run --target workflow:citation_audit --data examples/evaluation/citations.jsonl --no-judge --report .agent-workspace/citation-evaluation.json
```

在线评测与反馈自动入库默认关闭，开启后运行独立 `vertical-eval online` worker。用户明确修正的答案才会进入回归参考集；评分、评论保留为来源信息。离线优化必须提供不重叠的训练/验证集，生成候选后比较同一验证快照，只保存候选和报告，不替换生产配置。

### 可选动态路由

同时设置 `VA_ECONOMY_MODEL` 与 `VA_QUALITY_MODEL`，然后使用 `--task-tier economy|quality`。由 LangChain `wrap_model_call` 和 `request.override(model=...)` 在每次模型调用选择具体模型。生产系统应根据可信任务分类、实测准确率、上下文预算设置 task_tier；当前 CLI 仅提供人工指定的演示入口。Harness Profile 应分别测试模型适配，不假设所有 profile 设置会随单次模型覆盖自动重新装配。
