# Agent Harness

系统使用 Deep Agents 主智能体：网络检索、工作区文件工具、保存的 Python 脚本与原生子任务委派。

- 主循环使用 Deep Agents create_deep_agent，子任务继承工作区、敏感路径权限、审批和上下文预算。
- 不引入业务插件、MCP、技能注册、长期记忆、评估、监督者或自研工作流。
- 配置在 config/settings.py，组装在 core/compose.py；桌面 RPC 在 desktop.py。
- 文件操作在 storage/document_backend.py；文档阅读用 MarkItDown，编辑用 Python 格式库。
- 聊天、附件、产物和数据库属于用户数据；不得因代码清理删除。
- 身份、工作区边界、敏感文件拒绝、审批、超时和取消必须保留。
- 日志使用 log/logging.py，不记录用户内容、密钥或任意输入对象。
- 仅通过类提供业务接口；__all__ 仅出现在 __init__.py，不保留类方法或旧模块路径别名。
- 优先复用标准库和已安装依赖，不复制第三方实现，不为已删除 API 加兼容层。
- 验证：uv run pytest、uv run ruff check .、uv run mypy；desktop 下 npm run typecheck、npm test、npm run build。
- 不手动编辑生成的 openwiki/ 页面。
