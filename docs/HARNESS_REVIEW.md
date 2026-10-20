# Harness 源码对照与改造（2026-10-20）

参考源码下载到 `C:/Users/Hello/.codex/references/agent-harness/`，不进入产品依赖或仓库发布内容。

| 项目与固定版本 | 对照的实现 | 当前采用方式 |
| --- | --- | --- |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness/tree/687f282e8625c6b8d11ff58211a90524be2e4317) | `docs/subsystems/core.md`、`tools.md`：插件面向公共契约，具体 loop 可替换；工具执行、模型内容投影分开 | 继续使用 `DomainPlugin` / `PluginContribution` 和 LangChain `BaseTool`；将强制 Exa 客户端移出核心，作为 `web.search` 插件启停 |
| [pi](https://github.com/earendil-works/pi/tree/73f8de80bc87021375f8b525292ac07492253002) | `packages/coding-agent/docs/extensions.md`：加载声明与运行资源生命周期分开；shell 工具保留完整日志、限制模型输出 | 删除桌面提示词生成时的重复插件构建；脚本、完整日志保存到文件，模型只接收预览和路径 |
| [OpenCode](https://github.com/anomalyco/opencode/tree/d63c8d25f7edf8eb53bf6723e890abe311b91cfa) | `packages/core/src/tool/plugin/shell.ts`：工具本身是插件，命令退出码、超时、输出截断是明确结果 | 执行结果明确携带退出码、超时、截断标记和日志路径；不因工具调用返回就声称计算成功 |
| [Deep Agents](https://github.com/langchain-ai/deepagents/tree/a2f88434c5a4b5ce5244c468bb9f69b27831842d) / [LangChain](https://docs.langchain.com/oss/python/langchain/middleware/overview) | 原生图、工具、中间件、技能、子智能体及持久化组合 | 保留现有 `create_deep_agent` / LangGraph 组合；不移植另一套执行循环、事件总线或插件容器 |

## 落地边界

- 文件执行：现有 `write_file` / `edit_file` 保存普通 `.py` 文本，`execute_python_file` 只接收文件路径；不再暴露 `run_python(code)`。主 harness 和桌面端共用这一工具。
- 解释器：桌面优先随软件分发的 Python，其次用户配置；开发环境使用仓库 `.venv`。`HarnessSettings.python_executable` 经 `BuildServices` / `WorkflowContext` 原样传给工作流。
- 证据：data_analysis 生成的脚本实际调用统计计算函数；宿主执行后读取 JSON，再记录退出码与文件摘要。原先仅重写预计算结果的脚本不再被当成实际计算凭证。
- 插件：已有安装发现、allowlist、工具去重、技能门控、`open` / `aopen` 资源生命周期继续承担扩展。`web.search` 默认开启，无须手动启用；实际搜索时才创建 Exa 客户端，目录枚举只构造工具声明。显式 CLI 插件列表和桌面关闭设置仍受尊重。
- 提示词：统计库、画像 API、方法选择及科研报告规则移到 data_analysis 的技能文件；桌面宿主只保留通用执行与交付规则。领域说明按需读取，其他任务不再携带整段统计 SOP。
- 权限：文件执行继续经过现有桌面审批和只读模式。脚本使用当前用户权限，工作目录约束不是系统沙箱。

没有引入第三方框架的运行时、自动重试或热更新机制。没有测量真实模型的准确率、token 或速度提升；这些需要固定任务集和独立评测，不能用源码简化代替结论。

## 实际检查

- 桌面固定指令字符串由 3947 字符减少为 784 字符（约 80%）；统计规范移入技能文件，未将字符变化当作 token 或效果评测。
- 项目 `.venv`：完整 pytest 首次发现 1 项原有失败；排除该项后 **350 通过、1 排除**。失败项 `tests/desktop/test_attachment_content.py::test_limits_text_and_preserves_image_content` 期待旧的内嵌长文本截断；已从 HEAD 版本单独复现，未修改其行为或删除测试。
- 本次涉及模块的 Ruff 检查通过。全仓库 Ruff 仍有 3 项既存问题，位于未改动的 `storage/backends.py` 和 `data_analysis/__init__.py`。
- 默认 mypy（Python 3.11 目标）受已安装 NumPy stub 的 Python 3.12 语法阻断；使用 `--python-version 3.12` 后，配置中的 10 个源文件通过。
- 桌面 TypeScript 类型检查、构建通过；Vitest **103 通过、1 跳过**（真实模型测试未启用）。
- 已生成 `desktop/resources/python/python.exe`（Python 3.12.12，约 589 MB）。科学库和宿主模块迁移后导入通过；更新最终 wheel 后实际跑通文件执行、插件目录和 data_analysis 脚本计算。生成目录已忽略，不进入源码版本管理。
- 运行时打包脚本语法、wheel 构建与 `git diff --check` 通过。尚未生成 Electron 安装包，也未进行真实账号／真实模型验收。
