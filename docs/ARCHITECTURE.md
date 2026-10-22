# Agent Harness

以 `deepagents.create_deep_agent` 为主 Harness。CLI 和桌面共用 `core/compose.py` 中的 `AgentComposer`，由 Deep Agents 管理模型与工具循环、子任务委派、摘要和工具调用修复。宿主只保留模型配置、文档适配、桌面协议和权限策略。

目录位于 `packages/agent-harness/src/vertical_harness/`，按实际职责组织，复用框架提供的能力：

| 组件 | 实现与边界 |
| --- | --- |
| Agent Loop | `core/compose.py` 装配 Deep Agents；`cli.py` 和 `desktop.py` 负责宿主入口。 |
| Model Adapter | `core/routing.py` 配置 LangChain Provider，`middleware/routing.py` 按可信上下文选择模型。 |
| Prompt Builder | `core/compose.py` 组合基础提示与 `agent_content`，工具 schema 由框架生成。 |
| Instructions / Skills | 每次模型请求加载 `~/.agent-harness/AGENTS.md`，随后加载工作区 `AGENTS.md`；保留已发现子目录指令的刷新。原生 `skills` 加载 `~/.agent-harness/skills/` 和工作区 `.agents/skills/`，同名时项目覆盖全局；主 agent 和通用子 agent 共用。 |
| Context Manager | Deep Agents 摘要中间件在有效容量的 90% 压缩；`middleware/context_limit.py` 检查完整请求预算。 |
| Tool Registry / Executor | Deep Agents 注册和执行文件工具；`tools/` 提供检索、Python 脚本和用户交互。 |
| Session / State Store | `storage/checkpoint.py`、`storage/store.py` 管理检查点和状态存储的生命周期。 |
| Memory Manager | 当前使用会话历史和摘要；未配置独立长期记忆或知识检索。 |
| Orchestrator / Planner | Deep Agents 原生 `task` 委派与可选 `TodoListMiddleware`；子任务复用宿主权限、审批和预算限制。 |
| Runtime / Sandbox | `bridge/runtime.py` 配置宿主环境，`tools/python_execution.py` 执行已保存的脚本；脚本以当前用户权限运行。 |
| Guardrails / Permissions | `middleware/permission.py`、`middleware/approval.py` 和 Deep Agents 文件权限；只读模式禁止委派及副作用工具。 |
| Lifecycle Hooks | 使用原生 middleware 和 callback；`langchain/streaming.py`、`desktop.py` 处理流事件、超时、取消和审批恢复。 |
| Observability / Evaluation | `log/`、`langchain/callbacks.py`、`utils/usage.py` 提供日志、Trace 和用量统计；没有独立评估平台。 |

文件系统适配位于 `storage/document_backend.py`。Office/PDF 使用 MarkItDown 阅读，编辑和精确读取使用保存的 Python 脚本。敏感路径拒绝、工作区边界、身份、审批、超时和取消继续保留；文件工具权限不构成 Python 进程沙箱。

每个 skill 位于 `<skills>/<name>/SKILL.md`，使用包含 `name` 和 `description` 的 YAML frontmatter。技能元数据由 Deep Agents 读取并缓存在会话状态中，正文通过 `read_file` 按需加载；桌面端每条新用户消息会刷新元数据，审批恢复沿用原运行状态。全局技能映射为 `/.agent-harness/skills/`，该映射仅供文件工具读取，不开放全局目录的写入。缺失的指令文件和技能目录可省略。

桌面设置中的指令和技能管理直接读写上述目录，系统与具体项目分别展示；项目选择项使用实际目录名与完整路径，管理操作不切换聊天项目。技能 ZIP 包支持根目录 `SKILL.md` 或单层技能目录，保留附带文件，最多 512 个条目、解压总量 10 MiB，重名拒绝覆盖。导入完成全部路径和内容校验后才发布技能目录，编辑只修改 `SKILL.md`，删除移除整个所选技能目录。原有 `config/agent.md` 补充指令仍按原逻辑提供给 `agent_content`，不自动迁移。

业务接口直接通过类调用，`__all__` 只在 `__init__.py` 声明类导出。不保留 `engine/` 转发包、未接入运行链路的 `aop/`、类方法别名或 `sys.modules` 旧路径映射。四个独立 middleware 直接放在 `middleware/` 下。

聊天、附件、产物和数据库属于用户数据，代码整理不得删除。日志不记录用户内容、密钥或任意输入对象。优先复用标准库和已安装依赖，不复制第三方实现，不为已删除 API 加兼容层。不手动编辑生成的 `openwiki/` 页面。

验证命令：项目 `.venv` 中的 `python -m pytest`、`python -m ruff check .`、`python -m mypy`；桌面在 `desktop/` 下执行 `npm run typecheck`、`npm test`、`npm run build`。
