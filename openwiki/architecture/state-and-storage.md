---
type: concept
title: 状态管理、检查点与文档后端
description: 剖析 CheckpointFactory 会话检查点生命周期、StoreFactory 键值存储持久化以及 DocumentFilesystemBackend 文档读取与文件操作。
tags: [storage, sqlite, state-management, checkpoint, markitdown, document-backend]
sources:
  - id: openwiki-source-b2d3c4fe5f46c92d7d63b4ff
    resource: repo://packages/agent-harness/src/vertical_harness/storage/checkpoint.py
  - id: openwiki-source-98010c0db5dd2894595be8e5
    resource: repo://packages/agent-harness/src/vertical_harness/storage/document_backend.py
  - id: openwiki-source-65d4a856be05761cb8003001
    resource: repo://packages/agent-harness/src/vertical_harness/storage/store.py
verified:
  - by: openwiki/0.7.2
    at: 2026-10-21T02:48:18.086Z
generated: { by: "antigravity", at: "2026-10-21T02:48:18.086Z" }
---

# 状态管理、检查点与文档后端

在垂直领域智能体宿主中，状态持久化与文件交互构成了智能体与用户交互的基础。`vertical-agent-harness` 在 `vertical_harness.storage` 模块中提供了会话检查点工厂 `CheckpointFactory`、状态存储工厂 `StoreFactory`，以及基于 MarkItDown 的虚拟文件系统后端 `DocumentFilesystemBackend`。

## 1. 状态生命周期划分

根据 [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)，系统将状态清晰划分为检查点与文件产物两类：

1. **短期会话与执行图状态 (Checkpointer)**：
   - 包含当前会话消息序列、智能体执行计划与中间节点状态；
   - 关联具体的 `thread_id`；
   - 默认通过 SQLite 检查点保存器（`SqliteSaver` / `AsyncSqliteSaver`）实现持久化，测试与临时环境支持内存模式（`InMemorySaver`）。
2. **工作区产物与文档数据 (Document Filesystem Backend)**：
   - 包含用户输入文档、分析中间产物与生成成果；
   - 托管在由 `DocumentFilesystemBackend` 管理的物理工作区磁盘目录中（默认 `./.agent-workspace`）。
3. **用户数据资产保护硬约束**：
   - 聊天会话记录、上传附件、交付产物与 SQLite 数据库均属于不可替代的用户资产，任何代码清理与重构操作严禁删除。

## 2. CheckpointFactory 检查点生命周期 (`storage/checkpoint.py`)

[packages/agent-harness/src/vertical_harness/storage/checkpoint.py](../../packages/agent-harness/src/vertical_harness/storage/checkpoint.py) 负责管理 LangGraph 检查点保存器的生命周期：

- **连接串预备 (`_prepare_checkpoint_conn_string`)**：解析 `settings.resolved_checkpoint_target`，当为文件路径时自动递归创建父目录。
- **即用型检查点创建 (`create_checkpointer`)**：
  - 纯内存模式返回 `InMemorySaver()`。
  - SQLite 模式建立跨线程共享连接并执行 `checkpointer.setup()` 完成表结构初始化。
- **同步上下文管理器 (`open_checkpointer`)**：基于 `SqliteSaver.from_conn_string` 安全打开并执行 `setup()`，退出上下文时安全关闭连接。
- **异步上下文管理器 (`aopen_checkpointer`)**：基于 `AsyncSqliteSaver.from_conn_string` 管理异步生命周期。

## 3. StoreFactory 键值存储生命周期 (`storage/store.py`)

[packages/agent-harness/src/vertical_harness/storage/store.py](../../packages/agent-harness/src/vertical_harness/storage/store.py) 统一封装了原生 LangGraph `BaseStore` 的实例创建与连接管理：

- **模式切换**：根据 `settings.store_backend` 分流纯内存（`StoreBackendType.MEMORY`）与 SQLite（`StoreBackendType.SQLITE`）。
- **初始化 setup()**：建立连接时自动执行 `store.setup()`，确保底层键值表结构与索引就绪。
- **上下文管理**：提供同步 `open_store` 与异步 `aopen_store`，保障连接句柄的优雅释放。

## 4. DocumentFilesystemBackend 虚拟文档系统 (`storage/document_backend.py`)

[packages/agent-harness/src/vertical_harness/storage/document_backend.py](../../packages/agent-harness/src/vertical_harness/storage/document_backend.py) 继承自 DeepAgents 的 `FilesystemBackend`，实现了工作区文件系统协议与多模态文档格式提取：

- **Office 与 PDF 高保真 Markdown 读取**：
  - 支持 `.pdf`、`.docx`、`.xlsx`、`.xlsm`、`.pptx` 等多种主流文档格式。
  - 集成 MarkItDown 将二进制文档动态转换为结构化 Markdown 文本，并配备最近文档缓存（`_document_cache`）避免重复解析。
- **分页读取与文本保护**：
  - `read(file_path, offset, limit)` 支持行级分页读取，默认单次上限 2000 行，有效防止大文件撑爆大模型上下文窗口。
  - 读取时自动验证非二进制 UTF-8 编码，遇到非文本二进制文件安全提示智能体使用专用脚本处理。
- **原子化文件写入与精准局部编辑**：
  - `write` 操作采用临时文件加原子重命名（`replace`）机制，杜绝部分写入导致的文件损坏。
  - `edit` 操作提供基于行与字符串的精准匹配替换（`EditResult`），智能体只需提供最小唯一修改上下文，禁止全文件盲目重写。
- **工作区边界约束**：
  - 严格限制在 `root_dir` 内操作，禁止通过 `../` 目录穿越逃逸到宿主敏感目录。

## 相关页面

- [Harness 系统架构与核心组装](./overview.md)：了解状态后端在 AgentComposer 中的装配。
- [动态模型路由与模型适配](./model-routing.md)：查看状态上下文在模型路由中的应用。
- [桌面端集成与 JSON-RPC 通信](../integrations/desktop-workbench.md)：了解桌面事件循环中的检查点回放。
