# Agent Harness

系统仅保留一个 Agent：网络检索、工作区文件工具、保存的 Python 脚本。

- 主循环使用 LangChain create_agent，复用 Deep Agents 文件、摘要和工具调用修复中间件。
- 不引入业务插件、MCP、技能注册、长期记忆、评估、监督者或自研工作流。
- 配置在 config/settings.py，组装在 engine/compose.py；桌面 RPC 在 desktop.py。
- 文件操作在 storage/backends.py；文档阅读用 MarkItDown，编辑用 Python 格式库。
- 聊天、附件、产物和数据库属于用户数据；不得因代码清理删除。
- 身份、工作区边界、敏感文件拒绝、审批、超时和取消必须保留。
- 日志使用 logging.py，不记录用户内容、密钥或任意输入对象。
- 优先复用标准库和已安装依赖，不复制第三方实现，不为已删除 API 加兼容层。
- 验证：uv run pytest、uv run ruff check .、uv run mypy；desktop 下 npm run typecheck、npm test、npm run build。
- 不手动编辑生成的 openwiki/ 页面。
