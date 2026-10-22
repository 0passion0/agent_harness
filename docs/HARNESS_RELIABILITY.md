# Harness 可靠性改进

本次改动针对当前后端能复现的运行边界问题，继续使用 Deep Agents 的原生循环、摘要、工具执行与检查点。没有引入新依赖、模型调用服务或额外的多智能体编排层。

## 研究依据与工程判断

2026-10-22 使用 Exa 按 OpenAI 工具恢复、Anthropic 长任务案例、LangChain 中间件三个方向检索了 15 条候选结果，阅读了 7 篇官方页面。Context7 工具未在本次会话暴露；API 和执行顺序以本地 `deepagents 0.7.28`、`langchain 1.4.6`、`langgraph 1.2.16` 源码与回归测试交叉验证。这里只采用公开的 Codex/agent 实践，不假设其未公开内部实现。

| 官方依据或案例 | 对当前代码的判断与改动 |
| --- | --- |
| [Deep Agents 自定义文档](https://docs.langchain.com/oss/python/deepagents/customization) 明确同名中间件在原有位置替换，新增中间件插入核心栈之后。 | 原先传入列表里「路由、项目指令、摘要」的顺序不是实际顺序。现在用薄适配器占据原生摘要槽位，先完成路由与项目指令准备，然后直接调用原生摘要逻辑。 |
| [LangChain 上下文工程](https://docs.langchain.com/oss/python/langchain/context-engineering) 区分单次请求视图和持久状态，并将模型与工具选择视为上下文控制。 | 模型容量检查使用路由后的模型；项目指令参与压缩判断。只读模式提前隐藏写入、执行与委派工具，执行入口仍保留独立拒绝逻辑。 |
| [Anthropic 上下文工程](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) 将上下文视为有限资源，强调精简工具集合和保留重要状态。 | 复用原生摘要和历史归档；失效的动态指令替换为空间位置不变的提示，避免已保存的摘要截断索引错位、遗漏后续用户输入。 |
| [Anthropic 长任务案例](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) 记录了过早宣布完成、任务半途失去上下文和验证不足等失败模式。 | 用可执行场景验证真实组装图。达到步数边界后由图强制终止并明确标记剩余工作未完成，不能只靠提示模型停止。这里的强制终止是本项目的工程选择，并非照搬该案例的架构。 |
| [OpenAI Functions 恢复指南](https://developers.openai.com/api/docs/guides/agents-api/tools/functions) 要求有副作用的工具按调用标识保存结果，执行结果不确定时先核实再重放。 | 本次保留既有的流式重试边界：不重放已交付的不完整工具调用，审批恢复仍走原生检查点；相关回归继续执行。没有宣称实现跨崩溃的工具 exactly-once。 |

另核对了 [Deep Agents 摘要参考](https://reference.langchain.com/python/deepagents/middleware/summarization) 与 [LangChain 预置中间件](https://docs.langchain.com/oss/python/langchain/middleware/built-in)。它们支持优先复用原生能力；本次没有复制摘要算法或重写执行循环。

## 行为变化与验证场景

- **完整上下文先准备再压缩**：项目指令和历史单独均未越界、合计越界时，先压缩历史；切换到更小窗口模型时，在发送前根据所选模型容量压缩。原始检查点历史保留。
- **摘要索引稳定**：加载过的子目录指令文件被删除或变为不可读时，移除其旧内容但保留消息位置；恢复已有摘要的会话不会吞掉下一条用户输入。
- **只读工具集合**：正常模型只看到允许的工具；伪造或忽略工具列表的调用仍被执行入口拦截。`ask` 和 `full` 的审批、工具能力保持不变。
- **确定性终止**：`max_steps=N` 的第 N 次有效响应起禁用工具，最多额外给一次文字收尾机会；模型持续请求工具时由图结束。新用户输入重置预算，框架合成的续跑消息不重置。默认 `None` 不新增调用次数限制，运行超时与图递归上限仍保留。

新增场景位于 `tests/test_model_context.py` 和 `tests/test_harness_workflows.py`，均覆盖同步与异步路径。既有摘要归档、子任务权限与审批恢复、输出截断续跑、流式重试、取消测试用于检查兼容性。测试使用本地假模型与临时工作区，不产生模型 API 费用。

## 本次验证结果

在 Windows / Python 3.12.11 的项目环境中完成：

| 检查 | 结果 |
| --- | --- |
| `uv run --no-sync pytest --tb=short` | 475 通过，10 跳过。跳过项需要 Bun/OpenCode 参考环境或 Windows 符号链接权限。 |
| `uv run --no-sync ruff check .` | 通过。 |
| `uv run --no-sync mypy` | 项目配置范围内的 7 个源文件通过；该配置不覆盖全部后端。 |
| `git diff --check` | 通过。 |
| `uv build --all-packages --out-dir <临时目录>` | sdist 和 wheel 构建通过。 |
| 从生成的 wheel 直接导入后执行 `scripts/check_wheels.py` | 类型标记检查及原生图调用通过，确认没有导入源码目录中的包；依赖使用现有虚拟环境。 |

打包时发现源码缺少构建配置与检查脚本已要求的 `py.typed`，本次补齐此空标记文件。未修改依赖版本、数据库格式或桌面协议。

## 边界

Token 仍是近似估算；摘要不能消除不可压缩的超大系统提示或工具 schema，最终预算检查会拒绝请求。配置的摘要模型和保留阈值仍由构建时设置决定；极端窗口差异可能在压缩后继续超限并明确报错。本次没有用付费模型衡量任务成功率、时延或费用，不能将这些回归结果当作模型质量提升百分比。Python 与 shell 仍以宿主用户权限运行，文件工具权限不是操作系统沙箱。
