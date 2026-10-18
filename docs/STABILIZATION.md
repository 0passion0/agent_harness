# 核心稳定化与插件 v1 支持边界

本次按架构审查逐项修复，保留 Deep Agents / LangGraph 循环和包结构。本文是实现契约和升级说明，不表示任意真实模型、远端服务或多副本部署已经认证。

## 落实清单

| 审查项 | 实现 |
|---|---|
| A1 身份碰撞 | 文件记忆采用 file_memory_v2 + 原始 tenant/user/bucket 的 SHA256；拒绝空身份；显式复制工具保留源记录、拒绝目标冲突 |
| A2 纯监督 | 按 agent 过滤并拦截文件/执行工具（含 delete）；只允许受管工作流，拒绝 raw subagent；本地 task middleware 不含 general-purpose |
| A3 并行派发 | 同批更早的调用预留名额；重复幂等任务只执行第一个；拒绝/缓存不计入实际派发 |
| A4 异步状态 | 启动不算完成；不支持的依赖/幂等/结果 schema 组合构建即报错；监控核对任务与租户；禁止未经重新审批的 update |
| A5 路由 | 同一 middleware 实现 sync/async，覆盖两个 tier 与 invoke/ainvoke/astream |
| A6 分叉 | 复制根 checkpoint 的 pending writes；拒绝已有目标、非根 namespace 和可识别的静态/活动嵌套子图 |
| A7 数据目录 | 默认数据库在工作区旁；显式位于工作区内的数据库/sidecar/日志受工具权限保护；旧默认文件存在时拒绝静默切换 |
| A8 生命周期 | VA_PLUGIN_CONFIG + config_for；open_agent/aopen_agent 托管资源；插件可选 open/aopen；借用资源归调用者 |
| A9 组合规则 | 全量贡献检查缺失依赖/环，保留核心 middleware/任务工具名，manifest 要求 PluginManifest，原 exports 保留 |
| A10 发布 | uv.lock、依赖下界、Python 3.11/3.12 CI、测试/Ruff/边界类型检查/wheel 与非 editable 安装冒烟 |
| B1 有界执行 | 原生 step_timeout；CLI/评估/ainvoke_agent/astream_agent 总 deadline；不引入调度器和计费平台 |
| B2 核心独立 | 核心默认空插件名单，科研样例由 .env.example 显式启用 |
| B3 数字边界 | 引文 ID 最多 9 位，工具返回校验错误，工作流返回 invalid_input |
| B4 记忆 | 分页流选择最早 50 条、逐批重取；消费完的回执按保留期清理；取消等待在途记忆线程结束才释放 store |
| B5 评估 | 保留 required_tools 尝试指标，新增 required_successful_tools 成功回执指标 |
| B6 工程 | 两包 py.typed、skill/wheel 冒烟；mypy 从 contracts/config/namespace 开始，不批量删除 API |

## 数据迁移

新默认数据库为工作区旁的 `<workspace-name>.runtime/store.sqlite` 和 `checkpoints.sqlite`，可用 `VA_RUNTIME_DATA_DIR` 指定。文件 store 与 checkpointer 分别配置；内存 store 不再覆盖显式 checkpoint 文件路径。

若旧 `<workspace_root>/store.sqlite` 或 `checkpoints.sqlite` 存在，默认解析会报错，不移动、删除或静默忽略数据。可以显式配置 `VA_STORE_DB_PATH` / `VA_CHECKPOINT_DB_PATH` 暂时沿用原文件；工具层保护这些文件及 WAL/SHM/journal。迁移时停止写入者、备份并使用 SQLite backup API 导出到新目录，核对后显式设置运行目录或两个数据库路径。不要移动正在写入的数据库或遗漏 WAL。

旧文件记忆 namespace 可能已因字符清洗发生身份合并，不能自动推断归属。确认每条旧记录的身份、暂停相关写入，再显式复制：

```python
from types import SimpleNamespace
from vertical_harness import HarnessSettings, RunContext, open_store
from vertical_harness.storage import copy_memory_namespace, tenant_memory_namespace

settings = HarnessSettings()  # 必须明确指向已有数据库。
owner = RunContext(tenant_id="original.tenant", user_id="original.user")
destination = tenant_memory_namespace(SimpleNamespace(context=owner))
source = ("original_tenant", "original_user", "memories")  # 必须人工核对。
with open_store(settings) as store:
    count = copy_memory_namespace(store, source, destination)
```

复制保留源数据，相同目标值允许重试，冲突报错且不覆盖；已复制项保留，修正冲突后可重试。源/目标不得互为前缀。混合了多个身份的旧 namespace 须先人工拆分，禁止整包分配给一个用户。自动偏好/知识记忆使用原有独立摘要 namespace，不需此迁移。本次代码修改未自动迁移用户数据库。

## 构建与插件资源

持久化库调用使用 `with open_agent(settings) as agent` 或 `async with aopen_agent(settings) as agent`，只在上下文内执行。wrapper 拥有自己创建的 store/checkpointer、插件资源与 tracing，调用者传入的资源不关闭。同步 wrapper 不支持 MCP 或仅异步的资源插件。

`build_agent` 保留原生图返回类型。纯内存配置可省略 store；持久化配置必须显式传入托管 store 或改用 wrapper，避免无关闭入口的连接。CLI 正常执行统一使用异步托管入口。

插件 entry point factory 无参数，manifest 必须是 PluginManifest，`build(services) -> PluginContribution` 必须可重复且无连接/后台任务副作用；工作流发现、列表和评估元数据也会调用它。

插件用自己的 Pydantic schema 校验 `services.config_for(plugin_id)`。`VA_PLUGIN_CONFIG` 是按启用插件 ID 索引的 JSON；config_for 返回深拷贝，不修改其他实例配置。需要资源时额外实现同步 context manager `open(services)` 或异步 context manager `aopen(services)`，yield PluginContribution。异步宿主优先 aopen。运行时使用上下文贡献，不重复 build；build 仍提供纯声明元数据。插件上下文负责部分初始化失败清理，宿主 ExitStack 负责进入成功后的所有退出路径。普通插件不需要新基类。

现有工具、Skill、Middleware、Workflow、原生 subagent 承载扩展即可，不再设计九类插件。核心 middleware 保留名不允许插件覆盖。跨插件 workflow 通过稳定名称组合，安装依赖仍由 Python 打包工具处理。

## 调度与恢复语义

max_dispatches 根据检查点历史中的实际派发回执计数，策略拒绝与缓存命中不消耗次数。同批按照调用顺序预留，超过名额拒绝。相同幂等描述同批只执行首个，其余明确拒绝，可等首个成功后重试取缓存；不同工作流仍可并行。工具抛错但未留下回执时，原生恢复可能重试该逻辑请求；写服务必须有业务幂等键，缓存不是恰好一次保障。

async 工作流支持独立启动、查询、取消、租户与派发限制。依赖、幂等、显式 result_schema、被其他任务作为前置的组合暂不支持并明确拒绝；需要这些契约时使用 compiled workflow 或在远端完整编排。update_async_task 禁止执行，须提交新的可审查任务。远端 schema 声明不构成运行隔离。

纯监督模式不连接 MCP，不暴露插件直接工具，不允许 raw subagent，直接文件/执行工具既从模型请求隐藏也在执行前拒绝。无需全局 profile helper；原 helper 保留用于启动时的模型适配，不用于逐租户权限。

跨线程 fork 复制根图 pending writes，不覆盖已有目标。可识别的静态/活动嵌套子图暂时拒绝，使用同线程原生分支。同名目标的并发创建由宿主串行化，没有跨进程 compare-and-swap 保证。

**正常恢复与回放不同：** 对新线程不指定 checkpoint ID 的 invoke(None) 会复用成功节点的 pending writes；显式指定 checkpoint ID 是原生回放，可能重新执行后续步骤。前次审查探针显式指定了 ID，因此单凭重跑不能将原因全部归为 pending writes 丢失；本次分别验证。store 和外部副作用都不随回放回滚。

## 预算、记忆与信任边界

- VA_WORKFLOW_TIMEOUT_SECONDS=300 使用原生 graph.step_timeout，约束模型/工具 superstep；异步委派仍处于父工具步骤内。
- VA_RUN_TIMEOUT_SECONDS=900 约束 CLI、评估、ainvoke_agent、astream_agent 整次调用。审批恢复获得新的调用 deadline，不计算人工等待时间。
- 直接使用原生 invoke/ainvoke/astream 的宿主自行管理总 deadline；每步限制仍在。同步 Python 线程不能硬终止，工具要设提供商超时，必要时用隔离进程。超时不撤销已有副作用，不盲目重试写入。
- 记忆分页选最早 50 条，每批仍最多 32,000 字符，成功后重新取批次。通用 BaseStore 没有排序查询 API，当前每批有界扫描；超大持续积压再换索引查询。VA_MEMORY_EVENT_RETENTION_DAYS=90 使整合及活跃用户每日维护时清理过期且两类 inbox 均已消费的回执；未消费数据保留，窗口外旧消息重放可能重新入队。仅使用偏好的部署也在对话时触发维护，不需独立清理进程。
- 取消记忆 to_thread 操作会等待线程结束再传播取消，以保护 store 生命周期；同步模型仍须合理超时，不承诺立即硬取消线程。
- 进程内插件是可信 Python 代码，拿到原始 store；effect/schema/虚拟路径不是 OS 沙箱。宿主负责 tenant/user/thread/run 授权及独立工作区。不可信插件需受控进程/服务与 OS 隔离。

## 稳定 API 与验证

稳定候选面是 plugins.contracts、entry point、配置读取、托管入口与原生贡献协议；AgentComposer 内部字段不作为插件 API。旧 re-export 保留。任意 Runnable/远端 schema 不自动获得本地 StateGraph 的实际隔离验证。

required_tools 保持模型尝试调用含义。required_successful_tools 要求相应成功 ToolMessage；成功回执仍不代表事实正确，领域结果另用结构化参考与验证器。

门禁命令：`uv sync --frozen --all-packages --extra dev`、`uv run --frozen ruff check .`、`uv run --frozen mypy`、`uv run --frozen pytest`、`uv build --all-packages`。CI 配置 Python 3.11/3.12，在干净环境先安装核心 wheel，再安装科研插件；从源码工作区外运行 scripts/check_wheels.py，检查核心独立运行、entry point、skill、类型标记和最小原生图。

2026-10-18 本地 Windows / Python 3.12 验证：全量 182 项通过（包含原有的 18 项多模态测试，新增 31 项稳定化回归），Ruff 与上述 5 个源文件的 mypy 检查通过；两包 sdist/wheel 构建及工作区外独立安装冒烟通过。Python 3.11/Linux 矩阵待远端 CI 执行，不能由本地结果推定。

真实 provider/Agent Protocol/Postgres、压力、多副本及不可信代码沙箱仍需按实际部署验证；没有引入热卸载、依赖求解器、事件总线、新 Loop 或分布式调度。
