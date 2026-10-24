# 后端职责与运行边界

## 组织

后端位于 `packages/agent-harness/src/vertical_harness`。保留类接口和每文件一个类，通过具体依赖组合，不增加业务基类、工作流引擎或依赖注入容器。

| 模块 | 职责 |
| --- | --- |
| models、config、core/context | models 集中数据模型并统一导出；config 管非模型配置能力，RunContext 管可信身份 |
| execution | Shell/Python 执行、后台作业及共享环境过滤和进程树终止 |
| filesystem | 文件后端、文档提取、读写、搜索、补丁、格式化和技能资源 |
| storage | 原生 Checkpointer、Store 及连接生命周期 |
| middleware | 框架钩子、权限、审批、路由、裁剪、摘要、预算和执行限制 |
| langchain | 可序列化流事件、工具详情、日志回调 |
| core/compose | 装配模型、文件后端、工具和中间件；CLI 与桌面共同使用 |
| runtime | 项目资源、按 thread_id 隔离的并行运行及持久化会话操作 |
| bridge、desktop、cli | 宿主协议、输入转换与应用操作协调 |

数据模型从 models 包导入，其他能力从具体模块导入。底层不能导入桌面宿主；filesystem 不依赖 bridge，storage 不依赖文件工具，runtime 不依赖协议桥接。`tests/test_backend_boundaries.py` 与类结构测试约束这些边界。

## 资源所有权

`AgentRuntime` 持有配置周期的 Checkpointer、Store、Shell 执行器和可信身份。准备完整后才替换旧配置；构造失败回收新资源，不发布半初始化配置。资源由创建方关闭，借用方只结束自己的图调用和流迭代器。

`RunManager` 按 thread_id 持有各自的 ActiveRun，同一会话防止重复启动，不同会话并行。桌面宿主按租户和用户保留独立项目资源；切换项目只选择资源，不关闭后台任务。审批暂停以原生检查点为准。恢复必须匹配运行、中断、租户和用户身份。运行清理和事件流关闭后才发出终态；取消不进入普通失败重试。回调通过任务身份区分同一 run_id 的不同恢复执行。

会话删除按“取消并等待该会话运行、结束该会话后台作业、删除检查点与用量”执行。同项目配置资源地址不变时复用连接，仅替换后续运行设置；资源地址变化且仍有运行时明确拒绝替换。EOF 和关闭等待所有项目的运行清理，再关闭资源；单个资源关闭失败也继续清理其他资源。取消、重新配置、关闭均不删除工作区产物；显式会话删除仅删除该会话持久状态。

## 请求准备

保留 Deep Agents 原生循环、状态和摘要位置。请求先路由模型、加载系统和项目指令，再确定性裁剪旧工具结果，然后由原生摘要和预算检查处理；准备类提供 prepare/aprepare，不通过嵌套回调组装准备步骤。

裁剪只修改本次请求视图，落盘完整旧结果并保留恢复路径；最近结果与检查点原消息保持不变。摘要将历史视为不可信数据，保留量不超过 16,000 Token。发送前预算使用实际模型容量，超限抛出原生 ContextOverflowError。压缩和裁剪日志只记录结构化元数据。

原生子任务继承权限、指令、技能、审批和预算。只读模式同时限制模型可见工具和执行入口。模型重试不重放不完整工具调用，不承诺跨崩溃的副作用 exactly-once。

主图与每个声明式子图分别创建 Middleware 实例。增强文件工具按 `FilesystemMiddleware` 名称替换原生槽；目录指令使用自身的 `DirectoryInstructionsMiddleware.before_model` 节点名。工具审批使用官方 `HumanInTheLoopMiddleware(interrupt_mode="per_call")`，没有自定义审批调度器。完整栈顺序与 Hook 矩阵见 [Deep Agents 重构报告](DEEP_AGENTS_REFACTOR.md)。

`AgentComposer` 将借用的 Store 注入原生图；同步/异步上下文管理器管理自身创建的 Store 和 Checkpointer。`AgentStreaming` 每次执行创建独立观测回调，绑定 run/thread 日志上下文；不会将可变计数器挂到共享图配置。桌面读取、启动和删除已有会话前核对检查点身份，不自动认领无身份的历史线程。

## 文件与进程

`FileMutation` 共享跨实例写锁、协作取消与原子字节替换。文件编辑和资源保存共用同一实现；失败清除临时文件并保留原目标。Office/PDF 通过 MarkItDown 阅读，复杂编辑通过保存的 Python 脚本执行。

补丁在锁内先完整预检，再按操作顺序提交。applied 保留操作列表，files 每个最终路径只有一份真实差异；移动按旧路径删除和新路径结果报告。格式化失败但已经修改的文件也按实际内容报告。多文件 I/O 失败可能部分完成，错误明确列出已提交操作，不伪装为完整事务。

`ProcessControl` 只共享环境白名单与进程树终止；Shell 和 Python 保持不同的命令、超时与输出契约。后台状态由 ShellJob 承载，并按租户、用户和会话隔离。重复取消仍需等待清理结束。

Python、Shell 和格式化器使用宿主用户权限；文件工具的路径权限不是操作系统沙箱。

## 当前契约与验证

CLI 命令和工具参数保留，SQLite 表结构不变。Python 数据模型统一从 models 导入，其他能力直接从具体模块导入；审批中断使用原生 tool_approval，恢复按中断 ID 提交单个 approve/reject 对象。目录指令与合成消息只使用当前名称及 harness_* 标记，不解析历史别名。旧版暂停任务不能在新图中继续，升级边界见 [原生化优化记录](NATIVE_CLEANUP.md)。不删除、重写用户数据库或工作区文件。

运行项目 `.venv` 的 pytest、Ruff 和 mypy；mypy 覆盖整个 Python 包并要求方法注解。桌面运行 typecheck、对应主进程测试和构建。现有 Linux/Python 3.11、3.12 CI 保留，另在 Windows/Python 3.12 验证文件和进程关键链路。wheel 检查必须在非源码工作目录导入构建产物。

模型迁移、前后端并行会话、配置快照和验证说明见 [模型集中与并行会话](MODELS_AND_CONCURRENT_SESSIONS.md)。
