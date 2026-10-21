# OpenCode 通用任务 Harness 迁移记录

验证日期：2026-10-21。参考仓库 `D:/github/opencode` 固定于
`7d3752c14ce8dd03cfaac03ee4062a877808a69c`。下列源路径相对于 `packages/core/`。
目标源码根目录为 `packages/agent-harness/src/vertical_harness/`。

## 范围与验收状态

范围包括六个文件工具、Agent 构建与编排、工具注册、任务执行与验证、上下文/会话持久化、
权限审批、子任务和取消。Harness 保持通用：研究、文档、数据分析、自动化与软件任务使用同一张图。
代码编辑及仓库测试只是其中一种工作流，不自动要求其他任务运行代码测试。

已经交付可运行的算法迁移和 LangChain 集成；**不能标记为整个 OpenCode Harness 100% 行为等价**。
文末列出实际未迁移或未证明等价的行为。六工具的逐项源码、参数、性能和边界清单见
[文件工具迁移记录](opencode-file-tools-migration.md)。

## 源码到现有架构的映射

| OpenCode 能力 / 源码 | 当前实现及迁移方式 | 验证 |
| --- | --- | --- |
| Agent 构建、工具与子任务：`src/agent.ts`、`src/plugin/agent.ts`、`src/tool.ts`、`src/tool/runtime.ts` | 复用 `core/compose.py:AgentComposer` 唯一 `create_deep_agent`，原生 StructuredTool、ToolRuntime、general-purpose 子智能体；父子使用现有权限和同组 middleware | 真实 Agent 同步、异步、流式六工具与工作流；现有子任务/权限测试 |
| Agent Loop 与 durable 执行：`src/session/runner/llm.ts`、`execution.ts`、`run-coordinator.ts`、`step.ts` | 保留现有 LangGraph 状态图、AgentLoop、AgentStreaming、CheckpointFactory、RunContext；未新增执行循环或状态存储 | 既有流式、取消、checkpoint、审批恢复测试；不宣称复现源数据库事务顺序 |
| 基础提示、任务方法与验证：`src/session/runner/prompt/system.txt`、`src/session/system-prompt.ts`、`src/plugin/system-prompt/gpt-astra.txt` | 扩展现有 BASE_PROMPT，按任务要求验证；代码任务追踪调用者、局部编辑、检查测试/构建结果；文档/数据验证落盘结构与计算，研究核对来源 | 真实图写入/编辑/失败检查/修复/复验；另有非代码 CSV 分析及报告读回 |
| 项目指令：`src/config/plugin/instruction.ts`、`src/instruction-discovery.ts`、`src/session/context.ts`、`src/tool/plugin/read.ts` | 新增 `ProjectInstructionsMiddleware` 每次调用完整读取工作区根 AGENTS.md，并刷新文件工具已发现的附近指令；更新、删除、拒绝访问在下次模型请求中反映；保留原始 checkpoint 与 `agent_content` | 10 项专项，含更新/删除/重建/越界/拒绝、async worker、元数据保留；真实图更新后下一步可见，计入上下文预算 |
| 可选最大模型步数：`src/session/runner/llm.ts:220`、`step.ts:91` | 新增 `OpenCodeExecutionLimitsMiddleware`，直接计算 checkpoint 消息；达到 `VA_MAX_STEPS` 时 tool_choice=none、保留定义、忽略约束的调用返回错误；新用户消息重置预算 | 同步/异步真实图，保留工具绑定、新输入重置、禁止实际写入 |
| 输出 token 截断续写：`src/session/runner/llm.ts:284` | 同一 middleware 用原生 after_model/jump_to=model；最多两次 synthetic HumanMessage，源提示文本与第三次错误；工具调用仍走原图 | 同步/异步续写成功与连续三次 length 失败 |
| 模型重试：`src/model-retry.ts`、`src/session/runner/retry.ts` | 新增 `OpenCodeModelRetryMiddleware`，最多 10 次重试，2/4/8/10 秒退避与 ±20% 抖动，Retry-After 上限 900 秒，timeout 最多重试 3 次；SDK max_retries=0 防叠加；续写共用原重试预算 | 49 项专项；既有 16 项真实 SDK HTTP 离线测试转至原生 Agent 图，覆盖 invoke/ainvoke/stream/astream、连接/500/timeout/401 |
| 部分流恢复：`src/session/runner/step.ts:183`、`src/session/runner/llm.ts:277` | 最薄 `ModelOutputGuard` 原生 context-local callback 保留模型身份/状态与 callbacks/metadata；纯文本中断先存原生 AIMessage，再用源 synthetic 提示通过 after_model 跳回原图；不完整工具调用错误传播 | 真实 ChatOpenAI/OpenAI SDK SSE + create_agent.astream；第二次 HTTP 含部分文本和精确续写提示；checkpoint 持久化、内容交付一次、11 次请求预算、有状态模型及并发隔离 |
| 上下文与压缩：`src/session/compaction.ts`、`src/session/context.ts` | 复用已有 SummarizationMiddleware、ContextLimitMiddleware 与工具输出驱逐，不新增摘要模型或消息数据库 | 既有摘要/预算测试；保留目标 90% 阈值，源 reserve/overflow 策略未等价 |
| 前台与后台 shell：`src/tool/plugin/shell.ts:201`、`src/shell.ts:32`、`src/shell/select.ts`、`src/tool-output.ts` | 新增 `ShellExecution.tools()` 原生同步/异步 StructuredTool；前台 120000ms，后台默认无限时；后台 ID、status/wait/cancel/output/timeout/list/remove、可信身份隔离、最近 25 项完成记录；持久日志与末尾 50 KiB/2000 行预览；进程树清理 | 前台 10 pass/1 symlink skip，后台 18 pass；实际数据任务、分页/超时/取消/移除/隔离/保留上限/worker 异常；跨两次 Composer 上下文真实图继续等待并读取报告 |
| 运行资源生命周期：`src/session.ts:377`、`src/session/execution.ts:127` | 桌面 AgentLoop 持有共享 shell 实例；会话删除先取消并等待活跃运行，然后关闭该会话后台任务，再删除 checkpoint/store；重新配置、shutdown、EOF/协议错误关闭服务 | 4 项桌面实际进程测试，含其他会话不受删除影响、活跃运行 finalizer 顺序；普通 run.stop 保留显式后台任务 |
| 权限与交互：`src/permission.ts`、`src/file-access.ts` | 复用 DesktopPermissionMiddleware、FilesystemPermission 和 DesktopApprovalMiddleware；apply_patch、shell 纳入原风险审批；不新增权限管理器 | read_only 禁止执行；ask interrupt 后批准才执行；文件路径与敏感项不泄露 |
| 用户交互、计划、办公文档、脚本 | 保留现有 ask_user、TodoListMiddleware、Office/PDF、execute_python_file、web_search 及动态模型路由 | 现有测试 + CSV→保存分析脚本→执行→读取 JSON 报告真实图测试 |
| 格式化：`src/formatter.ts`、`src/formatter/builtins.ts`、`src/config/plugin/formatter.ts` | 新增 `OpenCodeFormatter` 与严格配置 schema；在现有文件锁内接入 Write/Edit/Patch，26 个内置检测按源顺序运行，成功后同步 BOM；Patch 全部写入后仅格式化唯一目标 | 60 项 formatter/后端/真实图测试；实际 TS 源 26 项顺序、扩展名、参数、环境对照；取消先停止进程树并等待 worker |
| 上下文溢出恢复：`src/session/compaction.ts` | 复用已安装 DeepAgents `SummarizationMiddleware` 的 ContextOverflowError 恢复，不新增压缩调度器 | `test_compose_summarization.py` 共 19 项，新增同步/异步真实图验证恢复请求更小、原始历史仍在 checkpoint 和归档 |

## 修改范围与兼容

- `core/compose.py` 装配 middleware/tools 并扩展通用任务提示，既有调用入口与图不变；新增可选 `shell_execution` 注入参数处理后台任务生命周期。
- `config/settings.py` 新增可选 `max_steps`，环境变量为 `VA_MAX_STEPS`，必须 >=1；默认 None 保留原无限额行为。新增 `formatter` / `VA_FORMATTER`（JSON），默认 None 与源未配置时相同，不执行格式化；true/{} 启用内置项，命名条目可覆盖 command/extensions/environment 或 disabled。
- `core/routing.py` 将 SDK 重试 5 改为 0，由上述图 middleware 统一接管。直接调用配置模型对象不再自带 SDK 重试；Agent 公开入口使用新策略。
- 新增 `middleware/project_instructions.py`、`execution_limits.py`、`model_retry.py`、`model_output_guard.py`；middleware 使用原生 ModelRequest、callback、消息、图跳转。
- 新增 `tools/shell_execution.py`；修改 `middleware/approval.py` 的既有写入风险集合；修改 `desktop.py` 连接共享 shell 实例及会话/进程清理，原 JSON-RPC 方法与载荷不变。
- 新增 `tests/test_opencode_harness_workflows.py`、`test_opencode_project_instructions.py`、`test_opencode_model_retry.py`、`test_opencode_shell.py`、`test_background_shell.py`、`desktop/test_background_lifecycle.py`；修改原 `tests/test_model_retry.py` 验证迁移后的 Agent 重试策略，未删除检查。
- 六工具代码及 ripgrep 核心依赖、MIT notice 清单见文件工具记录。无新增编排框架，无重复 Agent Loop，无新增 session 表或 checkpoint 格式。

shell 结果保留为结构化字典：`command, workdir, output_path, exit_code, timed_out, output, truncated`。
输出在 `/runs/shell/<id>/output.txt` 持久化后发布路径。可预期命令/目录输入错误通过
ToolException→error ToolMessage 供 Agent 修正；非零退出作为结果返回。未预期 spawn/I/O 错误直接传播。
工作目录与日志目录必须在工作区；命令仍以用户权限运行，与原 Python 工具一样不是操作系统沙箱。
环境使用原 Python 工具同类 allowlist，避免将模型密钥直接注入环境。

后台启动结果为 `shell_id, status, command, output_path`，`shell_status` 的 `action` 为
`status/wait/cancel`，后两者等待监控线程完成再返回最终字段。所有操作从 ToolRuntime 读取
`tenant_id/user_id/thread_id`，模型不能传入或改写归属；后台日志落盘后才返回路径。
桌面端共享实例跨轮次保留任务；独立宿主使用 `open_agent/aopen_agent` 时，默认在上下文退出清理。
需要跨上下文保留任务的宿主应注入同一 `ShellExecution` 并在释放工作区前 `close/aclose`。
直接调用 `build_agent` 的长期宿主也应使用该注入方式管理进程；没有第二套 Agent Loop。

`shell_status` 另支持 `output/timeout/list/remove`：`output` 使用字节 cursor/limit（默认 0/65536），
各页独立 UTF-8 replacement 解码；`timeout` 从调用时重新计时，0 清除；`list` 只列本会话运行中任务；
`remove` 使既有 wait 收到 NotFound，停止进程并等待监控退出后删除日志。自然完成任务保留最新 25 条索引，
自然淘汰不删除日志。上述接口仍复用同一个 StructuredTool 和会话归属校验。

## 实际验证

目标命令均用 `.venv\Scripts\python.exe`。以下为部分流恢复、后台生命周期及 Edit 性能优化后的实际结果。

| 命令 | 实际结果 |
| --- | --- |
| `-m pytest -o addopts='' -q` | **421 passed，1 failed，2 skipped，43.47 秒**。唯一失败是迁移范围外 `vertical_harness/__init__.py` 的顶层 `__getattr__` 违反既有结构规则（`tests/test_class_structure.py:49`）；未修改它或放宽测试。两个 skip 为 Windows 创建符号链接缺少权限。所有迁移功能测试通过 |
| `-m pytest -o addopts='' -q tests/test_opencode_harness_workflows.py` | 14 passed，含同步/异步/流式代码修复验证、非代码数据任务及跨 Composer 轮次后台任务 |
| `-m pytest -o addopts='' -q tests/test_opencode_model_retry.py` | 49 passed，含真实 SDK 同步/异步 SSE 纯文本中断续写、共享重试预算、未完成工具调用保护、模型身份/状态及 callback 保留、并发隔离 |
| `-m pytest -o addopts='' -q tests/test_opencode_shell.py` | 10 passed，1 skipped；跳过 Windows 缺少权限的目录符号链接测试；实际 junction 测试通过 |
| `-m pytest -o addopts='' -q tests/test_opencode_project_instructions.py` | 10 passed |
| `-m pytest -o addopts='' -q tests/test_background_shell.py` | 18 passed，含字节分页、重设/清除超时、list/remove、归属隔离、remove/wait 并发和工作区外日志删除保护 |
| `-m pytest -o addopts='' -q tests/test_opencode_formatter.py tests/test_opencode_formatter_integration.py` | 全量运行中 60 项通过，含源码双运行、配置、命令失败、BOM、Patch 格式化顺序/唯一目标、同步/异步原生图。取消测试通过子进程 ready 信号同步，避免依赖短 sleep 的调度竞态 |
| `-m pytest -o addopts='' -q tests/desktop/test_background_lifecycle.py` | 4 passed，真实进程验证删除/重新配置/关闭/EOF/协议错误与活跃运行清理顺序 |
| `-m ruff check .` | 通过 |
| `-m mypy` | 通过仓库配置覆盖的 7 个源码文件，非所有新增文件的完整类型验证 |
| `uv lock --check` / `uv build --all-packages` | 141 packages 锁检查通过；wheel/sdist 构建通过 |
| wheel 用 `uv pip install --python <项目.venv解释器> --no-deps --target <临时目录>` 安装后，从临时目录启动该解释器 | 最新 wheel 在全新临时目录确认导入来自安装目录；`AgentComposer.open_agent` 的异步图通过 CSV→配置格式化器→保存脚本→现有 execute_python_file→报告读回，结果 total=30；MIT notice 已打包。复用 `.venv` 其他依赖，非全新空环境安装。已有 execute_python_file 为异步工具，未改变其接口 |
| 既有 `scripts/check_wheels.py` 完整资产检查 | `missing vertical_harness/py.typed`，基线 HEAD 同样没有该文件；与上述 wheel 实际图测试分别记录，未顺带修改原打包基线 |
| 源 `packages/core`：`bun run test test/session-runner-retry.test.ts test/session-step.test.ts test/session-instructions.test.ts test/instruction-discovery.test.ts test/tool-shell.test.ts test/tool-output.test.ts` | 79 pass，63 skip，0 fail，311 expects，31.96 秒。跳过主要是源测试的 POSIX shell/signal 条件；未删除或强制改写测试 |
| 同目录 `bun run test test/formatter.test.ts` | 26 pass，0 fail，38 expects，13.12 秒 |

加上文件工具源测试 205 pass，本次实际源测试合计 **310 pass、63 skip**（16 个测试文件）。
源测试通过并不证明目标等价；实际双运行对照仅覆盖文件工具记录中列出的算法和样例。
模型测试为离线真实 SDK/原生图，不调用收费在线模型。Shell 进程与文件读写使用真实本机资源。

## 尚未完成、未等价或未验证

1. **formatter 安装与配置生命周期**：26 个内置检测/命令、配置合并算法、命令缓存、失败继续、BOM 与 Patch 最终内容已迁移。未实现源 Npm.which 的托管 npm 缓存/自动安装，当前查项目 node_modules/.bin 与 PATH，可由宿主注入 npm_which；源配置 watcher/transform hook 未接入。保留目标环境 allowlist 与严格 UTF-8，源继承全部环境并替换解码，仍非完全等价。
2. **部分流中的工具调用**：纯文本部分流已支持原生 checkpoint 续写；含未完成工具调用的部分响应仍直接传播错误，不执行或重放。源边生成边执行工具、delivery acknowledgement、重试耗尽时最后失败片段的持久化细节未等价。
3. **后台服务剩余语义**：已支持进程内后台任务、跨轮次查询/等待/取消、字节游标输出、运行中重设超时与 list/remove。尚无源完成通知自动注入 session、输出订阅、前台转后台交接或持久化 Job recovery/acknowledgement。目标内存任务注册表不跨重启；源 Shell 进程注册表也在内存，但 Job recovery 元数据另有 KV 持久化。源日志七天保留/每小时清理未迁移，目标日志随工作区保留。
4. **Agent/插件配置体系**：保留当前模型路由与 general-purpose 子任务，未迁移源动态 Agent 配置覆盖、所有内置 agent 类型、插件 hook 注册生命周期及完整配置层级。未建立平行注册系统来伪装等价。
5. **指令发现范围**：根与读取文件附近指令已接入，更新与删除在下一次模型请求刷新；checkpoint 历史保持原样。源全局/工作区外祖先指令、配置 URL、实时 watcher/removal 通知未迁移；已压缩为摘要的历史指令不能逐条撤回。
6. **压缩/会话语义**：保留目标原生 90% 摘要阈值和 checkpoint，已验证原生 ContextOverflowError 压缩后以更小请求恢复；源预留 token、摘要模板、多次缩小重试与 native compaction 选择策略仍不同。durable input queue、run coordinator 数据库提交顺序未证明等价。未将数据库 schema、私有事件协议强行搬入 LangGraph。
7. **权限粒度与 Shell 行为**：当前权限为 full/read_only/ask 与工作区路径规则；源 shell parser 的逐命令权限分类、login-shell 选择、外部目录审批仍不等价。环境过滤、cmd.exe/POSIX sh 选择沿用当前运行约束。Shell/脚本可访问用户有权访问的资源，路径约束不是命令沙箱。
8. **平台与性能**：未在 Linux/macOS 运行；Windows 一部分符号链接和源 POSIX 用例跳过。无模型/子任务/大目录端到端性能对照。经源行为对照的公共尾部优化将 1k 区域 Edit 从 44.44 ms 降至 4.43 ms；局部 Patch 本次约慢 1.36 倍；重复值跨边界的 LCS 仍二次方，完整数据见文件工具记录。
9. **源完整服务双运行**：未建立源完整插件服务与目标图的统一端到端 golden runner；现有算法对照、独立源测试和目标真实图测试不能替代它。

验收状态：可运行的通用 LangChain Harness 增强与文件工具算法迁移已交付；上述差异关闭之前，
不标记整个指定范围“完成迁移”或“100% 等价”。
