# Agent Harness

以 Deep Agents 的 `create_deep_agent` 为主入口，直接使用网络检索、文件工具、Python 脚本和原生子任务委派。保留模型选择、会话历史、流式输出、取消、审批恢复、上下文压缩和用量统计。子任务继承工作区、敏感路径权限、审批和上下文预算；只读模式禁止委派。

Python 接口统一通过类调用，例如 `AgentComposer.build_agent(settings)`、`AgentStreaming.astream_agent(...)` 和 `StoreFactory.open_store(settings)`。目录职责见 [后端架构](docs/BACKEND_ARCHITECTURE.md)，编码要求见 [后端规范](docs/BACKEND_CODING_STANDARD.md)。

最新稳定依赖、实际 Middleware 顺序、State/Runtime/持久化边界、扩展入口和验证结果见 [Deep Agents 重构报告](docs/DEEP_AGENTS_REFACTOR.md)。Pydantic 数据模型统一由 `vertical_harness.models` 导出。不同会话及项目可并行运行，切换会话、设置和文件操作不被后台任务锁定，详见 [并行会话说明](docs/MODELS_AND_CONCURRENT_SESSIONS.md)。新增业务工具可通过 `build_agent(tools=[...])` 注入，副作用工具用 `metadata={"requires_approval": True}` 接入原生逐工具审批。

## 开发

```powershell
uv sync --all-packages --extra dev --extra desktop
Copy-Item .env.example .env
# 填写 VA_MODEL、VA_API_KEY，按需填写 EXA_API_KEY
uv run vertical-agent "检查当前工作区文件"
cd desktop
npm ci
npm run dev
```

网络检索使用 Exa，缺少密钥时返回不可用提示，不影响文件和脚本。文本搜索使用 ripgrep；桌面运行时自带 rg。文件工具支持分页读取、定位、精确替换和原子写入，编辑保留原换行与 UTF-8 BOM。

Office/PDF 使用 Microsoft MarkItDown 提取 Markdown，仅用于阅读。修改、生成或精确读取使用保存的 Python 脚本，调用 openpyxl、python-docx、python-pptx、pypdf；科学计算库继续随桌面运行时提供。扫描件 OCR 和版式保真转换未包含。

脚本必须先保存为工作区 .py 文件，再调用 execute_python_file。脚本使用当前用户权限，保留超时、取消、输出日志和环境凭据过滤。文件工具的工作区和敏感路径检查不构成 Python 沙箱。

## 数据与升级

数据库默认位于工作区同级的 .runtime 目录，桌面使用应用数据目录。保留数据库表结构、项目文件、附件和产物。当前实现只接受原生逐工具审批，不保留旧审批协议、旧节点身份和旧消息标记。升级前先完成或取消旧版暂停任务并备份应用数据；旧暂停任务不能在新图中直接恢复。当前契约和调用迁移见 [原生化优化记录](docs/NATIVE_CLEANUP.md)。

## 验证与打包

```powershell
uv run pytest
uv run ruff check .
uv run mypy
cd desktop
npm run typecheck
npm test
npm run build
npm run test:smoke
npm run prepare:python
```

prepare:python 创建可移动的独立 Python 运行时，包含科学计算库和 rg。已有目标目录时会拒绝覆盖。
