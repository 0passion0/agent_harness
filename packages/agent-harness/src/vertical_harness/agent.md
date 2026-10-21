# Vertical Harness 核心命名空间 (vertical_harness) - 开发与规范指南

## 1. 模块定位与职责
`packages/agent-harness/src/vertical_harness/` 是智能体宿主脚手架（Agent Harness）的核心 Python 包。
基于 `deepagents.create_deep_agent` 组装单一主智能体，具备网络检索、工作区文件读写、受控 Python 脚本执行与原生子任务委派能力。宿主专注于配置注入、模型路由、前端通信协议、文档格式转换与安全拦截，核心 Agent 循环与委派委托给底层框架。

## 2. 目录架构与核心模块
包内按单一职责与业务领域组织，严格通过类对外提供能力，`__all__` 仅在 `__init__.py` 中声明：

- `__init__.py`：核心包公开 API 唯一门面，全量显式导出核心类（如 `AgentComposer`、`AgentLoop`、`HarnessSettings` 等），不包含模块级别名或兼容转发函数。
- `cli.py`：交互式命令行与测试执行入口，支持直接提问、工作区指定与流式输出。
- `desktop.py`：桌面端 JSON-RPC 通信协议适配器，`AgentLoop` 负责任务生命周期、流事件分发、人工审批恢复与超时取消。
- `bridge/`：桌面端通信桥接：
  - `attachments.py` (`AttachmentContent`)：多模态附件解析与内容提取。
  - `messages.py` (`DesktopMessages`)：JSON-RPC 协议消息打包与格式化。
  - `runtime.py` (`DesktopRuntime`)：桌面环境配置、环境变量与工作区初始化。
- `config/`：类型化配置体系：
  - `settings.py` (`HarnessSettings`)：智能体全局运行配置模型。
  - `checkpoint_config.py` (`CheckpointConfig`)：检查点持久化与会话标识配置。
  - `langsmith_settings.py` (`LangSmithSettings`)：链路可观测性配置。
  - `store_backend.py` (`StoreBackendType`)：键值存储后端类型枚举。
  - `task_tier.py` (`TaskTier`)：分级任务模型配置。
- `core/`：智能体组装与模型路由核心：
  - `compose.py` (`AgentComposer`)：装配 `create_deep_agent`、文件/检索/脚本工具栈、安全中间件与原生子智能体委派。
  - `routing.py` (`ModelRouting`)：LangChain 模型 Provider 实例化与推理参数配置。
  - `context.py` (`RunContext`)：不可变运行上下文与租户身份凭证。
- `langchain/`：LangChain 流式与回调管理：
  - `streaming.py` (`AgentStreaming`)：流式事件解析、工具调用状态追踪与审批中断处理。
  - `stream_event.py` (`StreamEvent`)：流式输出契约事件数据模型。
  - `callbacks.py` (`WorkflowLoggingHandler`)：模型调用与工具执行生命周期回调。
- `log/`：可观测性与安全日志：
  - `logging.py` (`HarnessLogging`)：结构化日志输出，严格执行隐私脱敏，禁止记录用户原文与密钥。
  - `observability.py` (`TracingSession`)：LangSmith 上下文追踪会话生命周期管理。
- `middleware/`：安全拦截与控制中间件：
  - `approval.py` (`DesktopApprovalMiddleware`)：高风险工具调用人机协同审批拦截。
  - `context_limit.py` (`ContextLimitMiddleware`)：请求预算与 Token 上限熔断控制。
  - `permission.py` (`DesktopPermissionMiddleware`)：工作区文件操作越界防御与敏感路径过滤。
  - `routing.py` (`ModelRouterMiddleware`)：运行时模型动态调优与上下文适配。
- `storage/`：持久化与文档处理后端：
  - `checkpoint.py` (`CheckpointFactory`)：SQLite 与 Memory 检查点生命周期管理。
  - `document_backend.py` (`DocumentFilesystemBackend`)：基于 MarkItDown 的 Office/PDF 文档解析与文件读写后端。
  - `store.py` (`StoreFactory`)：状态键值持久化存储工厂。
- `tools/`：运行时受控工具集：
  - `python_execution.py` (`PythonExecution`)：受控保存脚本执行工具（以当前用户权限运行，非沙箱）。
  - `search.py` (`WebSearch`)：网络检索工具封装。
  - `tool_details.py` (`ToolDetails`)：工具元数据解析与权限需求判定。
  - `user_interaction.py` (`UserInteraction`)：桌面端用户人机交互确认工具。
- `utils/`：辅助工具函数：
  - `structured_formatter.py` (`StructuredFormatter`)：终端与 UI 结构化展示辅助。
  - `usage.py` (`UsageEstimator`)：Token 消耗预估与用量统计。

## 3. 架构与工程规范（九大硬约束对齐）
1. **单一智能体与原生委派**：
   - 主循环使用 `deepagents.create_deep_agent`，严禁引入业务插件、MCP、技能注册、长期记忆数据库、独立评估流水线或重型自研工作流。
   - 子任务通过框架原生 `task` 委派，强制继承父任务的工作区路径、敏感路径拒绝列表、人工审批机制与上下文预算。
2. **纯类接口与精简门面**：
   - 业务接口统一通过类静态方法或实例方法调用，杜绝模块级别名（如 `build_agent = AgentComposer.build_agent`）及旧路径兼容层（无 `engine/` 或 `aop/` 转发）。
   - `__all__` 仅在各子包 `__init__.py` 与顶层 `vertical_harness/__init__.py` 维护，且仅导出公共类名。
3. **零隐私泄漏（Zero-Leakage）**：
   - 日志与追踪中严禁输出用户对话原始内容、密钥、Token 或任意未过滤的复杂参数。
4. **受控文档与沙箱认知**：
   - Office/PDF 文件统一使用 MarkItDown 读取转为 Markdown；复杂表格、精准修改由智能体编写完整保存的 `.py` 脚本使用 `openpyxl` / `python-docx` 等库执行。
   - Python 脚本执行以当前宿主用户身份运行，文件工具权限控制不构成 OS 进程沙箱。
5. **用户数据资产保护**：
   - 聊天会话记录、附件、生成的交付产物及本地数据库属于用户核心资产，任何重构、清理代码动作严禁误删。
6. **极简主义与失败立见（Fail-Fast）**：
   - 严禁对内部可预期数据添加无意义的防御性 `try-except` 包装；内部协议契约清晰，有错必爆。
   - 优先复用标准库与已安装核心依赖，避免过度抽象与投机性单实现封装。

## 4. 验证与测试命令
所有验证必须使用项目本地虚拟环境：
- 单元测试：`.\.venv\Scripts\python -m pytest`
- 代码风格与静态检查：`.\.venv\Scripts\python -m ruff check .`
- 类型检查：`.\.venv\Scripts\python -m mypy`
- 桌面端检查（在 `desktop/` 目录下）：`npm run typecheck`、`npm test`、`npm run build`
