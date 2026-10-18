---
type: concept
title: 测试架构与质量保证
description: 分析基于 pytest 的契约单元测试、安全防御校验（防目录穿越与严格模式）及 SQLite 存储生命周期回归测试。
tags: [testing, pytest, quality-assurance, regression, contracts]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T03:23:43.651Z
sources:
  - id: openwiki-source-83ccb301171788f9ae087a4a
    resource: repo://tests/test_schema.py
  - id: openwiki-source-09d2a8f36f3ecb7ab9487ab2
    resource: repo://tests/test_store.py
generated: { by: "antigravity", at: "2026-10-18T03:23:43.651Z" }
---

# 测试架构与质量保证

`vertical-agent-harness` 贯彻“测试驱动与确定性验证优先”原则。系统并未将业务正确性简单托付给不可控的模型自由生成，而是围绕数据契约、安全防御边界、多租户存储隔离与引用核验算法编写了严密的确定性测试套件。

## 1. 测试套件结构

仓库中的核心单元测试集中在 [tests/](../../tests/) 目录下：

```text
tests/
├── test_schema.py    # 数据契约、输入校验、安全防御与配置测试
└── test_store.py     # 原生 SQLite 存储生命周期、异步操作与复合后端隔离测试
```

运行全部测试：
```bash
pytest -q
```

## 2. 数据契约与安全防御测试 (`test_schema.py`)

在 [tests/test_schema.py](../../tests/test_schema.py) 中，针对系统边界定义了多组关键回归测试：

### 2.1 严格输入防御 (Fail-Closed)
- **拒绝未知字段 (`test_schema_rejects_extras`)**：验证当外部向 `ReferenceCheckInput` 传入未声明字段（如 `unexpected=True`）时，Pydantic 必须抛出 `ValidationError`，防范非预期参数注入。
- **引用异常精准捕获 (`test_reference_mismatch`)**：传入正文引用 `[1]` 与 `[3]`、但参考文献列表仅有 2 篇的输入样本，断言状态判定为 `issues`，且精确包含 `("out_of_range", 3)` 与 `("uncited", 2)`。

### 2.2 防目录遍历与标识符校验 (`test_plugin_contract`)
- **恶意插件 ID 拦截**：断言包含路径遍历字符的插件标识符（如 `../../evil`）无法通过 `PluginManifest` 校验。
- **恶意技能路径拦截**：断言包含 `..` 相对跳转符的技能路径（如 `../hack/SKILL.md`）必须被 `SkillAsset` 字段校验器拦截，防止攻击者读取或覆盖宿主私有文件。

### 2.3 业务规则与模型配置测试
- **已核实结论必须有证据 (`test_verified_finding_requires_evidence`)**：验证 `AuditFinding` 当 `status="verified"` 时若证据为空，必须抛出校验异常。
- **模型显式配置检查 (`test_settings_require_explicit_model`)**：在清除环境变量 `VA_MODEL` 的情况下，实例化 `HarnessSettings` 必须因缺少必填项而报错，禁止静默回退到默认模型。

## 3. 存储生命周期与隔离测试 (`test_store.py`)

在 [tests/test_store.py](../../tests/test_store.py) 中，对底层存储和文件系统的解耦机制进行了深入验证：

### 3.1 同步与异步 SQLite 生命周期
- **跨会话持久化 CRUD (`test_native_sqlite_store_lifecycle_and_crud`)**：
  - 会话 1：通过 `open_store` 写入用户偏好键值数据，上下文退出并关闭连接。
  - 会话 2：重新调用 `open_store` 打开数据库，验证数据可被正确重载读取，并在执行 `delete` 后返回 `None`。
  - 文件物理验证：断言工作区根目录下确实生成了 `store.sqlite` 文件。
- **异步存储验证 (`test_native_async_sqlite_store_lifecycle`)**：通过 `aopen_store` 异步上下文管理器验证对 `AsyncSqliteStore` 的 `aput` 与 `aget` 操作。

### 3.2 复合后端存储路由与字符清洗
- **工作区与记忆隔离 (`test_composite_backend_routes_memories_to_sqlite_store`)**：
  - 普通文件写入（如 `/draft.txt`）直接在本地磁盘工作区中创建物理文件。
  - 长期记忆文件（如 `/memories/AGENTS.md`）通过 `CompositeBackend` 被路由重定向至 `SqliteStore`，**磁盘上绝不会创建 `memories/` 物理文件夹**。
  - 命名空间清洗验证：针对包含点号的租户 ID `lab.a` 与用户 ID `researcher.7`，断言其在存储中被自动清洗为 `lab_a` 与 `researcher_7`，避免破坏 LangGraph 命名空间分隔。

## 4. 质量保证与 CI 扩展建议

根据 [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)，在迈向生产级部署时，质量保证体系应进一步扩展：
1. **LangSmith 链路追踪与评测**：接入 LangSmith Trace，对 Agent 运行延迟、成本和模型调用树进行可观测性监控。
2. **黄金数据集评测 (Evaluator-Optimizer)**：维护 50–100 个典型学术文本样本构成的黄金基准集，结合确定性规则与 LLM-as-judge 对引用审计准确率进行回归评估。

## 相关页面

- [插件机制与贡献契约](../architecture/plugin-system.md)：了解插件在白名单与清单校验上的契约约束。
- [状态隔离与分层存储](../architecture/state-and-storage.md)：了解 `SqliteStore` 与 `CompositeBackend` 的具体实现。
- [数字引用审计端到端流程](../workflows/citation-audit.md)：查看被测端到端流程。
