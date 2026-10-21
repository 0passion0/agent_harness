# OpenCode 文件工具迁移记录

验证日期：2026-10-21。参考仓库为 `D:/github/opencode`，固定提交为
`7d3752c14ce8dd03cfaac03ee4062a877808a69c`。以下源路径默认相对于其 `packages/core/`；
明确列出的 `packages/util/` 路径相对于参考仓库根目录。
目标环境实际版本：LangChain 1.4.6、langchain-core 1.6.12、LangGraph 1.2.16、DeepAgents 0.7.28。

六个文件工具的核心算法及现有 Agent 集成已实现；**尚未满足 100% 可观察行为等价验收**。
格式化器安装/配置生命周期、协议展示、编码和权限差异，以及源工程双运行对照的覆盖范围，见下文。
原有 Office/PDF 提取、`ls`、`delete`、模型路由、Checkpoint 与审批调用方式继续保留。
扩展后的通用任务 Harness 迁移范围、编排映射及差异另见 [Harness 迁移记录](opencode-harness-migration.md)。

## 源码与功能清单

目标源码根目录为 `packages/agent-harness/src/vertical_harness/`。

| 功能及边界 | OpenCode 源码依据 | 目标实现 | 验证 |
| --- | --- | --- | --- |
| Read 文本分页、2000 行/50 KiB、每行 2000 UTF-16 单元、下一页及越界反馈 | `src/tool/read-filesystem.ts` 的 `textPage`、读取服务；`src/tool/plugin/read.ts` | `tools/opencode_read.py:OpenCodeRead.read_text_page`，`storage/document_backend.py:read`；零基 API 适配 | `test_opencode_read.py`：空行、空文件、分页、超长行、字节预算、越界、实际源函数对照 |
| 二进制识别、UTF-8/BOM、CRLF/LF | 同上 | `OpenCodeRead.read_text_page`，保留后端严格 UTF-8、去 BOM、通用换行约定 | NUL、无效 UTF-8、BOM、CRLF；编码差异见下文 |
| 目录分页、目录优先、隐藏项、符号链接与坏链接 | 同上 | `OpenCodeRead.directory_entries`，`middleware/filesystem.py:_read_directory` | 目录及分页集成；超范围返回空页；符号链接测试受 Windows 权限限制 |
| 图片/PDF 签名识别、20 MiB 上限、原生媒体块 | 同上 | `OpenCodeRead.read_supported_media`，middleware `_read_media` | PNG/JPEG/GIF/WebP/PDF，扩展名不可信、文件增长、Agent 原生媒体；PDF 使用 `native_media=True` |
| Unicode 文件名恢复、唯一性、缺失文件候选 | `src/tool/plugin/read.ts` 的路径恢复及建议分支 | `OpenCodeRead.canonical/recover_read_path/missing_read_message`，middleware `_read_path` | NFC、空格/弯引号、同名歧义；候选重新鉴权，隐藏敏感文件名 |
| 读取后附近 AGENTS.md 发现、去重、上下文注入 | `src/tool/plugin/read.ts` 的 instruction 相关调用链 | middleware `before_model/abefore_model`，复用消息 reducer/checkpointer | 同步、异步及持久消息去重；不另建 Agent 状态 |
| Glob 默认 100、hidden、目录限制、忽略规则、`.git` 排除、截断 | `src/tool/plugin/glob.ts`，`src/ripgrep.ts:glob` | `tools/opencode_search.py:RipgrepSearch.aglob/glob`，`FileGlobSchema` | 临时 Git 仓库、隐藏文件、正向 glob、忽略项、限制、错误路径 |
| Grep 正则/字面量、大小写、文件/目录、include glob、默认 100 行 | `src/tool/plugin/grep.ts`，`src/ripgrep.ts:grep` | `RipgrepSearch.agrep/grep`，`SearchSchema`；保留 `glob/max_count/output_mode` | regex 错误、Unicode、大小写、单文件、隐藏文件、忽略规则、截断 |
| Grep 字节 offset、submatches 上限 100、2000 UTF-16 预览 | `src/ripgrep.ts` 的 JSON 解析与 `MAX_SUBMATCHES` | `tools/opencode_grep_result.py`，`RipgrepSearch`；经权限过滤后进入 `ToolMessage.artifact` | Unicode 字节偏移、BOM 解码后的偏移、子匹配、超长预览、敏感路径元数据不泄露 |
| 搜索进程生命周期、30 秒超时、有界 stderr、取消及提前停止 | `src/ripgrep.ts` 的 scoped process、`src/filesystem.ts` 的工具限制 | `RipgrepSearch._run/_collect/_stderr`，asyncio subprocess | 真实 rg 搜索；受控进程验证超时/取消回收，限制后停止；无 shell 拼接 |
| Edit 逐层匹配、精确命中优先、弯引号/破折号、行尾空白 | `src/tool/plugin/edit.ts` 的匹配辅助函数及 execute | `tools/opencode_edit.py:OpenCodeEdit`，后端 `edit/aedit` | 84 组源函数对照，匹配缺失、多处歧义、replace_all、空串/相同串 |
| Edit CRLF/LF 位置映射、LCS 行对齐、替换次数、BOM | 同上 | `OpenCodeEdit`，后端保留 CR-only 编辑兼容；仅在尾部值不跨边界重复时裁剪公共尾部 | 8,442 组实际源行映射与混合换行对照（含穷举、500 组随机）；BOM；显式 CR→LF 仍可执行 |
| Write 创建/覆盖、缺失父目录、BOM 保留与单一 BOM、内容换行不自动改写 | `src/tool/plugin/write.ts`，`src/file-mutation.ts`，其 BOM 辅助模块 | 后端 `write/awrite` 与已有 `_atomic_write` | 源 BOM/write 辅助函数对照：8 组、23 次连续写入；创建/覆盖 × fsync/replace 故障保护 |
| Patch 解析 Add/Delete/Update/Move、heredoc、Environment ID、EOF | `packages/util/src/patch.ts` | `tools/opencode_patch.py:OpenCodePatch.parse_patch`，`PatchHunk/UpdateChunk` 数据类 | 语法/错误测试、80 组实际源解析/应用函数对照 |
| Patch 四级上下文寻找、EOF 锚定、多 chunk 逆序应用、末尾 LF、BOM | `packages/util/src/patch.ts` | `OpenCodePatch.derive_update` 及辅助方法 | 500 组 seek 对照、前后空白、Unicode 排版、上下文失败、插入与删除 |
| Patch 全部 hunk/源和移动目标鉴权、解析及编码预检后再写；同文件更新串接 | `src/tool/plugin/patch.ts` 的 prepared/updates | 后端 `apply_patch`，middleware `_apply_patch/_aapply_patch` | 最后一个 hunk 缺失、越界、非法 Unicode 或被拒绝移动目标均不写前面的文件 |
| Patch 顺序提交、部分失败反馈、移动先写目标再删源 | 同上 | 后端 `apply_patch`，保留已完成资源列表；不伪装多文件事务 | 第二次写失败、移动、覆盖、删除及重复更新；部分成功副作用明确报告 |
| Patch 最终落盘内容 Diff、增删计数、无末尾换行 | 同上及 file diff 辅助模块 | 后端 `apply_patch`，`ToolMessage.artifact.files/applied` | 真实 Agent 元数据、重复更新最终 Diff、update 后 delete 的提交后读失败 |
| 文件锁、BOM 写入、生命周期 | `src/file-mutation.ts` 的 keyed mutex | `storage/file_mutation.py:FileMutation`，跨后端实例共享排序锁、可回收锁对象；沿用原子替换 | 两后端并发编辑；重复 async cancel 等待 worker 结束，取消后的待提交文件不落盘 |
| Write/Edit/Patch 格式化、命令检测与缓存、失败继续、BOM、Patch 最终内容 | `src/formatter.ts`、`src/formatter/builtins.ts`、`src/config/plugin/formatter.ts`、三个文件工具的调用点 | `tools/opencode_formatter.py`、`opencode_formatter_entry.py`，现有后端内接入；默认关闭、true/{} 启用 26 个内置项，配置经 HarnessSettings 传入 | 60 项 formatter 与集成测试；实际 TS 源 26 项顺序/扩展名/参数/环境双运行；成功/失败部分修改、BOM、全部写入后格式化唯一目标、真实 Agent 调用、取消结算 |
| 工具注册、权限、审批、结果/异常传播 | 六个 `src/tool/plugin/*.ts`，`src/file-access.ts`、`src/permission.ts` | `AgentComposer` 现有图 + `OpenCodeFilesystemMiddleware` + `StructuredTool/ToolRuntime/ToolMessage`；现有审批增加 apply_patch | 六工具同步/异步/流式实际图调用、read_only、ask 拒绝/批准、checkpoint resume |

## LangChain 集成和兼容约定

`AgentComposer` 仅将原 `FilesystemMiddleware` 替换为其局部子类；仍由 `create_deep_agent`
创建原来的 LangGraph 图。未增加 Agent Loop、调度器、重复 checkpoint 或独立工具分发系统。
`ToolRuntime` 注入、现有路径验证、FilesystemPermission、搜索结果格式化、工具输出驱逐、
原生消息/媒体块及既有审批继续使用原组件。`apply_patch` 纳入既有写入风险审批集合。

| 工具 | 对外参数和结果 |
| --- | --- |
| `read_file` | 保留 `file_path, offset=0, limit=2000`；增加 `native_media=False`。文本继续使用现有行号展示；图片使用原生 image 块；PDF 默认继续文本提取，可显式返回 file 块。 |
| `glob` | 保留 `pattern, path`；增加 `hidden=False, limit=100`。虚拟路径及 DeepAgents 展示格式保持一致。 |
| `grep` | 保留 `pattern, path, glob, output_mode, max_count`；增加 `literal=True, case_sensitive=True`。设 `literal=False` 使用 rg 正则。artifact 含经过权限过滤的源结构匹配信息。 |
| `edit_file` | 保留 `file_path, old_string, new_string, replace_all=False` 和 `EditResult`。 |
| `write_file` | 保留 `file_path, content` 和 `WriteResult`。 |
| `apply_patch` | 新增 `patch_text`；成功内容列出 A/M/D，artifact 包含 `applied` 与 `files`；可预期的解析、权限和文件系统错误转为 error ToolMessage，取消继续传播。 |

## 修改清单

- 修改 `core/compose.py`、`middleware/approval.py`：连接现有图和审批。
- 修改 `storage/document_backend.py`：复用已有文档提取、路径边界与原子写入，接入算法/锁/异步取消；新增 `storage/file_mutation.py`。
- 新增 `middleware/filesystem.py`，以及 `file_glob_schema.py`、`search_schema.py`、`native_read_schema.py`、`patch_schema.py`：现有工具接口适配。
- 新增 `tools/opencode_edit.py`、`opencode_patch.py`、`opencode_read.py`、`opencode_search.py` 和三个单类数据文件 `opencode_grep_result.py`、`opencode_patch_hunk.py`、`opencode_update_chunk.py`。
- 新增 `tools/opencode_formatter.py`、`opencode_formatter_entry.py` 和两个 formatter 测试文件；`config/settings.py` 新增可选 `formatter` / JSON 环境变量 `VA_FORMATTER`，默认 None 不执行格式化。
- 新增六个 `tests/test_opencode_*.py` 和 `tests/bench_opencode_file_tools.py`；未删除、跳过或弱化原有测试。
- 修改包 `pyproject.toml`、`uv.lock`：已有版本范围的 `ripgrep-bin` 从 desktop extra 移入核心依赖，核心包独立安装也能搜索；未升级其他依赖版本。
- 新增 `THIRD_PARTY_NOTICES.txt` 并加入 wheel，保留 OpenCode MIT 许可及版权声明；新增本文。

## 实际验证

以下 Python 命令均使用本项目 `.venv`，目标验证在仓库根目录执行。

| 命令/操作 | 实际结果 |
| --- | --- |
| `.venv\Scripts\python.exe -m pytest -o addopts='' -q` | 文件工具阶段为 **253 passed, 1 skipped**；扩展 Harness 后的最新全量结果见 Harness 迁移记录。工作区另一任务修改的 `vertical_harness/__init__.py` 顶层 `__getattr__` 违反既有结构规则；未修改它或放宽测试。Windows 符号链接测试受 WinError 1314 权限限制。 |
| `.venv\Scripts\python.exe -m pytest -o addopts='' -q tests/test_opencode_edit.py tests/test_opencode_patch.py tests/test_opencode_read.py tests/test_opencode_search.py tests/test_opencode_write.py tests/test_opencode_filesystem_integration.py` | 最新迁移专项 **89 passed, 1 skipped**，8.61 秒，包含算法对照、真实图调用和安全边界。 |
| `.venv\Scripts\python.exe -m ruff check .` | 通过。 |
| `.venv\Scripts\python.exe -m mypy` | 通过；仅仓库配置覆盖的 7 个 source files，不能视作新模块全量类型检查。 |
| `git diff --check HEAD -- <迁移涉及的文件>` | 迁移范围通过。当前全工作区检查还报告另一任务 `doc/tongliga/2026-10-21-fix-tool-details-non-command-failure.md` 中的行尾空格，未修改该文件。 |
| `uv lock --check` | 通过，141 packages。 |
| `uv build --all-packages` | wheel 和 sdist 均成功。 |
| 将 wheel 用 `uv pip install --python <项目.venv解释器> --no-deps --target <临时目录>` 安装，并从临时目录启动该解释器 | 确认导入来自已安装 wheel；`AgentComposer.open_agent` 实际执行 `apply_patch → grep`，落盘内容及 ToolMessage 成功；检查 wheel 含迁移算法、MIT notice 和核心 ripgrep 依赖。其余依赖复用 `.venv`，不是全新空环境依赖安装测试。 |
| 既有 `scripts/check_wheels.py` 的完整资产检查 | **失败：missing vertical_harness/py.typed**。基线 HEAD 也无此文件，属于原有打包问题；未顺手增加文件改变无关基线。上述图验证独立执行并通过。 |
| 源仓库根目录 `bun install --frozen-lockfile --filter '@opencode/core' --ignore-scripts` | 安装 Core 测试依赖成功；仅生成被忽略的依赖目录，`git status --short` 仍为空。未修改源锁文件或运行安装脚本。 |
| 源仓库 `packages/core` 下 `bun run test test/tool-read-filesystem.test.ts test/ripgrep.test.ts test/tool-search.test.ts test/tool-read.test.ts` | 补齐依赖后 **81 pass，0 fail**，211 expect() calls，6.22 秒。 |
| 同目录 `bun run test test/tool-edit.test.ts test/tool-write.test.ts test/tool-patch.test.ts test/file-mutation.test.ts test/patch.test.ts` | **124 pass，0 fail**，324 expect() calls，1.99 秒；合计六工具相关源测试 **205 pass**。 |
| 同目录 `bun run test test/formatter.test.ts` | **26 pass，0 fail**，38 expect() calls，13.12 秒。 |

对照测试不是根据文档重写的期望输出：Edit/Patch/Read/BOM 测试读取固定提交的真实 TypeScript，
使用 Bun.Transpiler 抽取可独立运行的源函数，与相同输入的 Python 结果比较。
Edit 有 84 组替换、8,442 组内部行映射/换行对照；Patch 有 80 组、另有 500 组固定随机 seek 对照；Read 有 7 组分页对照，
Write 有 8 组连续写入场景。适配层的 BOM/末尾换行等差异单独断言，并未假称相等。
Formatter 使用真实源 make() 与 Effect 执行 26 个内置项的 enabled，注入同一可控检测环境，逐项比较顺序、扩展名、参数及环境；未启动全部 26 种实际语言格式化器。
这些测试在源目录或 Bun 缺失时会跳过；本次实际运行中它们已执行通过。
搜索与媒体使用真实临时文件和 rg；**未运行完整源插件服务的端到端双运行对照**。

性能命令：`.venv\Scripts\python.exe tests/bench_opencode_file_tools.py --repeats 30 --large-repeats 10`。
相同输入先校验相同输出，再分别计时；10,000 行 CRLF 文本共 108,890 字符。

| 场景 | Python / ms | Bun 源实现 / ms | Python tracemalloc peak / bytes |
| --- | ---: | ---: | ---: |
| 10k 行中局部 Edit | 1.593 | 2.034 | 721362 |
| 10k 行中局部 Patch | 1.714 | 1.256 | 703204 |
| 1k 行区域 Edit | 4.427 | 7.175 | 864526 |

这些是本机微基准，不含模型、磁盘、格式化器或 Agent 图开销；没有 Bun 内存/RSS、
Read/Grep 大目录性能数据。公共尾部优化经实际源对照后，区域 Edit 从优化前 44.44 ms 降到 4.43 ms；
该场景快于源，局部 Patch 仍约慢 1.36 倍，不能宣称全场景无性能退化。

## 未完成或存在差异的项目

1. **格式化器剩余差异**：已迁移 26 个内置检测、命令执行/缓存/失败继续、配置合并算法，以及成功后的 BOM 同步和 Patch 最终内容；通过现有文件工具权限/审批后执行。
   未实现源托管 Npm 缓存/自动安装，当前查项目 node_modules/.bin 与 PATH，宿主可注入 npm_which。
   源配置 watcher/transform hook 未接入；当前使用明确的 HarnessSettings 配置。保留环境 allowlist、严格 UTF-8，与源继承环境/替换解码不同。
2. **协议按现有 API 适配**：源工具名/字段为 `read/edit/write/patch`、`path`、`oldString/newString/replaceAll`、`patchText` 等；目标沿用上述
   LangChain 接口。保留零基 offset、严格 UTF-8、去 BOM/通用换行、Grep 字面量默认值、现有 output_mode、
   虚拟路径和工具展示。源 TaggedError 与权限交互不会原样重建，使用现有结果对象/ToolMessage/审批机制。
3. **权限边界更严格**：维持当前工作区真实路径边界及 `.env`/存储文件拒绝规则，不复制 OpenCode 审批后
   访问外部目录或其他 worktree 的能力。符号链接越界仍拒绝。权限过滤前已受搜索结果数量限制，
   被拒项可能占用结果预算，不自动继续搜索补满。
4. **读取实现与展示**：目录使用 Python 确定性字典序，源使用 localeCompare；非 ASCII 名称顺序可能不同。
   源大文件以 256 KiB 块/文本树定位，目标用有界流式扫描；深 offset 时间仍随前缀长度增长。
   源 TextDecoder 的替换解码与目标严格 UTF-8、BOM/换行/末尾空行处理不同。
   原 PDF 默认文本提取保持不变，原生 PDF 需显式选择。附近 AGENTS.md 会注入；扩展 Harness 阶段新增根目录 AGENTS.md 每次模型调用前读取，已发现附近指令也会刷新更新/删除，checkpoint 历史不改写，原 `agent_content` 继续保留。全局/工作区外祖先指令与源监听机制仍未迁移。
5. **Diff 与 UI**：Patch 使用标准库 unified_diff，未逐字复现源 jsdiff 的 hunk 划分、共同缩进裁剪及界面。
   Write/Edit 保留既有结果协议，未提供源预览审批中的全部 Diff/existed 等元数据。
   不移植按 GPT 模型选择隐藏 edit/write 或 patch 的源 hook，六工具持续由当前图管理。
6. **并发与失败**：文件锁只覆盖本进程迁移工具，不能阻止外部编辑器/其他进程修改，也不新增源中不存在的
   stale-read 校验。保留原有解析路径后的 TOCTOU 局限，未用跨平台句柄沙箱重写文件系统。
   多文件 Patch 与源一致，提交阶段失败/取消可留下此前已提交的文件，没有整体回滚；同文件原子替换继续保护已有内容。
7. **资源与平台验证缺口**：源逐行 LCS 的最坏复杂度仍为二次方；极大替换区域可能耗时/占内存。
   rg 单条 JSON 超长匹配记录的内存未另设硬上限。未验证所有 Windows 共享锁/杀毒干扰、Linux/macOS
   行为、特殊设备文件、所有符号链接场景或完整源服务生命周期。上述目标回归冲突与打包基线问题仍需解决。

验收结论：已交付可运行、可通过现有 LangChain Agent 调用的六工具实现及上述验证证据；
源全量对照与这些未迁移/不等价项目关闭前，不应标记“100% 完成迁移”。
