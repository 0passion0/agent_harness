# 存储与文档后端模块 (vertical_harness/storage) - Agent 指南与规范

## 1. 目录定位与职责
`vertical_harness/storage/` 负责智能体运行图状态持久化、会话检查点管理以及工作区文档文件系统的协议适配。
提供统一的存储生命周期工厂与基于 MarkItDown 的多格式文档智能解析能力。

## 2. 核心类与文件
- `__init__.py`：统一导出存储核心类 `CheckpointFactory`、`DocumentFilesystemBackend`、`StoreFactory`。
- `checkpoint.py` (`CheckpointFactory`)：
  - 检查点持久化工厂：根据配置初始化 `InMemorySaver` 或 `SqliteSaver`，管理会话状态保存与回放。
- `document_backend.py` (`DocumentFilesystemBackend`)：
  - 文件系统虚拟后端，遵循 `deepagents` 的 `BackendProtocol` 协议。
  - 集成 MarkItDown 将 Office（docx/xlsx/pptx）与 PDF 文档转为高保真 Markdown，供智能体语义阅读与检索。
  - 提供基于绝对路径校验的文本文件精确读写与局部替换支持。
- `store.py` (`StoreFactory`)：
  - 键值持久化存储工厂：为智能体状态提供内存或 SQLite 键值存取生命周期管理。

## 3. 关键交互与设计约束
1. **用户数据资产绝对保护**：
   - 会话检查点数据库、用户上传附件、工作区产物及聊天记录均为核心用户资产，严禁在重构、测试或清理代码时执行物理删除。
2. **严格工作区路径边界**：
   - `DocumentFilesystemBackend` 严格限制在指定 `workspace_dir` 内操作，禁止通过相对路径或符号链接穿越访问工作区外部。
3. **精准阅读与保存脚本编辑分工**：
   - Office/PDF 文档优先通过后端转换为 Markdown 供大模型快速阅读；精确表格修改或文档复杂生成交由智能体保存并运行独立 Python 脚本完成。
4. **统一生命周期释放**：
   - 数据库连接与文件系统资源需配合上下文管理器安全打开与关闭，确保无句柄残留。
