# Deep Agents 垂直领域插件化 Harness（参考架构）

本仓库采用两个**独立 Python distribution**：通用 `vertical-agent-harness` 和演示插件 `vertical-plugin-research-citation`。

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

也可使用 uv workspace：`uv sync --all-packages` 后 `uv run --package vertical-agent-harness vertical-agent --list-plugins`。

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
VA_LOG_FILE=./.agent-workspace/logs/harness.log
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

## 插件零改主流程

插件必须在自己的 `pyproject.toml` 声明：

```toml
[project.entry-points."vertical_agent.plugins"]
"research.citations" = "research_citation.plugin:ResearchCitationPlugin"
```

主包仅通过 `importlib.metadata.entry_points` 扫描显式启用的插件。插件提供 Pydantic 校验的 `PluginManifest` / `PluginContribution`；主包组合原生工具、SkillsMiddleware、Subagents、LangChain Middleware 和 HITL。安装插件后重启服务加载；**不是热加载**。新插件使用新的 distribution name 和 entry point，不需要修改主流程源码。

## 核心技术选择

- Agent 运行：`deepagents.create_deep_agent()`；无自制 Agent Loop。
- Skills：`SKILL.md` 及 `SkillsMiddleware`；演示插件采用 `metadata.include_tools` 按需暴露工具，要求 `deepagents>=0.7.22`。
- 工作空间：开发使用 `FilesystemBackend(virtual_mode=True)`；并非安全 OS sandbox。
- Durability：默认 `InMemorySaver` 仅供演示。生产必须注入持久化 Checkpointer（Postgres）和 Store，按租户隔离。
- 评测：LangSmith 官方 Trace/Dataset/Evaluator；业务规则继续用确定性 Pydantic/pytest 检查。
- 权限：带写操作的工具必须 `approve/reject`，真正越权保护仍须工具后端逐次执行 RBAC/ABAC。插件本身是受信任的 Python 代码。

## 边界与不足

1. 此仓库是**参考实现**而非已投产系统；当前没有提供生产数据库迁移、队列、认证、审计服务、异步 MCP 生命周期、外部文件解析或 Web API。
2. 当前 CLI 单次运行创建 `InMemorySaver`，退出后不能跨进程恢复；需要服务型进程 + 持久化 Checkpointer。
3. 默认 `context_schema` 使用 Python dataclass 以匹配 LangGraph runtime，外层环境配置与插件契约使用 Pydantic v2。
4. 示例引文工具只检查数字引用与文末文献表的索引对应关系，不做真实性或证据支持验证。
5. 安装与完整 Agent 运行需相应官方包及 LLM 凭据；框架 API 可能演进，部署时应锁定并测试依赖版本。

详细设计参见 `docs/ARCHITECTURE.md`。

### 可选动态路由

同时设置 `VA_ECONOMY_MODEL` 与 `VA_QUALITY_MODEL`，然后使用 `--task-tier economy|quality`。由 LangChain `wrap_model_call` 和 `request.override(model=...)` 在每次模型调用选择具体模型。生产系统应根据可信任务分类、实测准确率、上下文预算设置 task_tier；当前 CLI 仅提供人工指定的演示入口。Harness Profile 应分别测试模型适配，不假设所有 profile 设置会随单次模型覆盖自动重新装配。
