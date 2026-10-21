---
type: concept
title: 测试体系与质量验证
description: 分析基于 pytest 的核心单元测试、类结构断言测试、受控文件后端测试、流式事件测试及桌面端自动化验证。
tags: [testing, pytest, quality-assurance, regression, class-structure, deepagents]
sources:
  - id: openwiki-source-514d063d3e68d0bfa50ee4a0
    resource: repo://tests/test_class_structure.py
  - id: openwiki-source-45067006fb2fec631673a66d
    resource: repo://tests/test_deepagents_harness.py
verified:
  - by: openwiki/0.7.2
    at: 2026-10-21T02:48:18.086Z
generated: { by: "antigravity", at: "2026-10-21T02:48:18.086Z" }
---

# 测试体系与质量验证

`vertical-agent-harness` 贯彻“测试驱动与确定性验证优先”原则。系统针对核心架构硬约束、单一主智能体装配、文档文件系统操作、流式事件分发及桌面端交互编写了严密的自动化回归测试套件。

## 1. 测试套件结构

仓库中的核心单元测试集中在 [tests/](../../tests/) 目录下：

```text
tests/
├── conftest.py                   # 全局 pytest fixtures 与环境初始化
├── test_class_structure.py       # 纯类接口封装与 __all__ 架构硬约束断言
├── test_deepagents_harness.py    # deepagents.create_deep_agent 单一智能体装配与原生委派测试
├── test_compose_summarization.py # 90% 上下文容量压缩摘要中间件触发测试
├── test_filesystem_tools.py     # DocumentFilesystemBackend 与受控文件工具测试
├── test_logging.py               # 结构化安全日志脱敏与运行上下文追踪测试
├── test_model_call_limit.py      # 模型调用次数上限熔断防御测试
├── test_model_content_budget.py  # 请求 Token 预算与内容截断测试
├── test_model_retry.py           # 模型网络重试与指数退避测试
├── test_multimodal.py            # 多模态附件转换与图像 Base64 编码测试
├── test_observability.py         # LangSmith 追踪会话生命周期测试
├── test_stabilization.py         # 确定性并发稳定性与状态隔离测试
├── test_store.py                 # StoreFactory 与 CheckpointFactory 持久化生命周期测试
├── test_streaming.py             # AgentStreaming 流式事件分发与中断处理测试
├── test_workflow_logging.py      # LangChain 回调生命周期事件追踪测试
└── desktop/                      # 桌面端专属 Python 桥接集成测试
    ├── test_attachment_content.py
    ├── test_bridge.py
    ├── test_document_backend.py
    ├── test_python_execution.py
    ├── test_runtime.py
    └── test_tool_events.py
```

## 2. 核心架构约束断言 (`test_class_structure.py`)

[tests/test_class_structure.py](../../tests/test_class_structure.py) 是系统架构硬约束的看门狗，确保重构与开发过程中不发生架构退化：
- **纯类接口断言**：断言 `vertical_harness` 所有导出接口均为类，严禁包含孤立的模块级转发函数或已废弃别名（如 `build_agent = AgentComposer.build_agent`）。
- **`__all__` 规范断言**：断言模块内部 `__all__` 仅包含显式公共类名，无函数别名。
- **废弃包隔离断言**：断言旧版本中的 `engine/` 与 `aop/` 转发包已被完全物理删除，不存在隐式 `sys.modules` 映射。

## 3. 单一智能体与委派测试 (`test_deepagents_harness.py`)

[tests/test_deepagents_harness.py](../../tests/test_deepagents_harness.py) 覆盖了基于 `deepagents.create_deep_agent` 的核心功能：
- **智能体图装配**：验证 `AgentComposer.build_agent` 成功组装主智能体图，挂载 `FilesystemMiddleware`、`SummarizationMiddleware`、`ContextLimitMiddleware` 等中间件栈。
- **原生子任务委派**：验证 `subagents` 包含 `general-purpose` 智能体，且子智能体强制继承主智能体的工作区物理根路径、敏感路径拦截规则与审批配置。
- **沙箱规则校验**：断言对 `/**/.env`、日志文件与 SQLite 数据库文件的读写请求被安全沙箱拒绝。

## 4. 文件系统与文档后端测试 (`test_filesystem_tools.py`)

[tests/test_filesystem_tools.py](../../tests/test_filesystem_tools.py) 验证基于 MarkItDown 的受控文档后端：
- **文档读取与缓存**：验证 Office/PDF 二进制文件被正确提取为 Markdown 文本，并测试 LRU 缓存命中机制。
- **分页与行截断**：测试通过 `offset` 与 `limit` 参数实现文本分页读取，防止超长大文件溢出。
- **原子修改 (`edit_file`)**：验证基于最小唯一旧字符串的精准局部替换与回滚保护。

## 5. 存储生命周期测试 (`test_store.py`)

[tests/test_store.py](../../tests/test_store.py) 覆盖状态持久化机制：
- **SQLite 与 Memory 切换**：测试 `StoreFactory.create_store` 在内存模式与 SQLite 模式下的表结构自动初始化（`setup()`）。
- **同步与异步上下文**：通过 `open_store` 与 `aopen_store` 验证跨会话数据的写入、读取与自动清理。

## 6. 验证执行命令

所有后端验证命令必须在项目本地虚拟环境中执行：

```powershell
# 运行全部单元测试
.\.venv\Scripts\python -m pytest

# 代码风格与静态规范检查
.\.venv\Scripts\python -m ruff check .

# 核心模块类型检查
.\.venv\Scripts\python -m mypy
```

桌面端自动化检查（在 `desktop/` 目录下）：
```powershell
npm run typecheck
npm test
npm run build
```

## 相关页面

- [Harness 系统架构与核心组装](../architecture/overview.md)：了解测试所保障的系统架构。
- [快速入门指南](../quickstart.md)：了解本地环境配置与运行。
- [桌面端集成与 JSON-RPC 通信](../integrations/desktop-workbench.md)：了解桌面端测试套件。
