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
