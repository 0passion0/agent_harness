---
type: concept
title: 学术引用审计插件实现
description: 深入分析 vertical-plugin-research-citation 演示插件的内部架构，涵盖纯函数解析器、确定性评估器、ResearchCitationToolkit、数据契约、技能资产与证据优先审计模型。
tags: [plugin, research-citation, validation, pydantic, evidence-first]
sources:
  - id: openwiki-source-50c13e861f5096759403fa57
    resource: repo://packages/research-citation/src/research_citation/checker/evaluator.py
  - id: openwiki-source-2b379b13d8964e154e5bfa5d
    resource: repo://packages/research-citation/src/research_citation/checker/parser.py
  - id: openwiki-source-1e0140ac00118fc045901433
    resource: repo://packages/research-citation/src/research_citation/checker/tools.py
  - id: openwiki-source-9983d19816e0ce12cc36840a
    resource: repo://packages/research-citation/src/research_citation/models/audit.py
  - id: openwiki-source-721e3ae67db867a56eacdc71
    resource: repo://packages/research-citation/src/research_citation/plugin.py
generated: { by: "antigravity", at: "2026-10-19T02:30:03.285Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-19T02:30:03.285Z
---

# 学术引用审计插件实现

`vertical-plugin-research-citation` 是基于垂直领域插件标准规范实现的学术手稿引用核查插件。该插件作为独立的 Python distribution（安装包），通过标准入口点接入宿主 Harness，演示了如何在不修改主工程代码的前提下注入纯函数确定性算法、LangChain `BaseToolkit`、Pydantic 契约校验模型与 `SKILL.md` 知识资产。

根据 [packages/research-citation/src/research_citation/__init__.py](../../packages/research-citation/src/research_citation/__init__.py)，插件内部划分为清晰的模块结构，并通过 `research_citation` 顶层包统一对外导出：
- `research_citation.workflow`：确定性 LangGraph 学术引用核查工作流（`citation_audit`）、三层状态隔离契约与工作流贡献工厂。
- `research_citation.plugin`：领域插件入口点实现（`ResearchCitationPlugin`）与技能资产加载。
- `research_citation.checker`：纯函数引用解析器（`parser.py`）、确定性集合比对评估器（`evaluator.py`）与 LangChain 工具包（`tools.py`）。
- `research_citation.models`：数字引用核查输入/输出模式（`check.py`）与证据驱动的学术审计报告模型（`audit.py`）。

## 1. 插件结构与构建贡献 (`plugin.py`)

在 [packages/research-citation/src/research_citation/plugin.py](../../packages/research-citation/src/research_citation/plugin.py) 中，`ResearchCitationPlugin` 实现了 `DomainPlugin` 协议：

- **清单声明 (`PluginManifest`)**：插件标识符为 `research.citations`，接口版本为 1，语义化版本为 `0.1.0`。
- **静态技能读取与缓存 (`_load_skill_asset`)**：结合 `@lru_cache(maxsize=1)` 与 `importlib.resources.files` 安全读取并缓存打包在 wheel 包内的 `numeric-citation-audit/SKILL.md` 内容，构造经路径校验的 `SkillAsset`。
- **贡献载荷组装 (`build`)**：
  - **确定性工作流导出**：通过 `build_citation_workflow_contribution(mode=self._workflow_mode)` 导出预编译 LangGraph 工作流 `citation_audit`，原生挂载为 `CompiledSubAgent` 或 `AsyncSubAgent`。
  - **工具包导出**：实例化 `ResearchCitationToolkit()`，通过 `ToolContribution.from_toolkit(toolkit, exposure=Exposure.SKILL)` 将 `scan_numeric` 工具批量声明为门控暴露模式（`Exposure.SKILL`），仅在模型激活对应技能后才暴露该工具（纯 Supervisor 模式可通过 `include_tools=False` 禁用）。
  - **技能挂载**：提供 `[_load_skill_asset()]`，供宿主挂载至虚拟 `/skills/research-citations-numeric-citation-audit/SKILL.md` 路径。
  - **指令注入**：向 Agent 提示词追加专属指令，引导主智能体委派 `citation_audit` 工作流或使用 `numeric-citation-audit` 技能，并仅汇报已核实的不一致问题。

## 2. 确定性引用审计 LangGraph 工作流 (`workflow.py`)

在 [packages/research-citation/src/research_citation/workflow.py](../../packages/research-citation/src/research_citation/workflow.py) 中，插件实现了完全确定性的 LangGraph 状态图 `citation_audit`，不引入任何额外 LLM 开销：

- **三层状态隔离**：
  - `CitationInputState`（`input_schema`）：接收调用方传入的 `manuscript_text` 与 `bibliography`，或兼容提取 `messages` 消息体中的文本与 JSON 代码块。
  - `CitationInternalState`（`InternalState`）：内部保存经入口节点严格校验的 `validated_input`、校验失败原因 `validation_error` 与比对结果 `evaluation_result`，禁止外部直接读写。
  - `CitationOutputState`（`output_schema`）：向 Supervisor 返回最终格式化的 `structured_response` 字典与包含审计报告的 `messages`。
- **状态图编排**：由 `extract_and_validate`（参数提取与 Pydantic 校验）-> `evaluate`（纯函数集合比对）-> `format_output`（构建结构化响应）顺序流转至 `END`。

## 3. 纯函数解析器、评估器与核查工具 (`checker/`)

插件将引用核查拆分为无状态的解析层、评估层与工具包装层：

### 3.1 方括号数字引用解析器 (`checker/parser.py`)
在 [packages/research-citation/src/research_citation/checker/parser.py](../../packages/research-citation/src/research_citation/checker/parser.py) 中：
- **语法正则提取**：通过 `_BRACKET_PATTERN`（`\[([0-9,\s\-]+)\]`）提取行内引用片段，并按逗号拆分为独立 token。
- **单序号与闭区间展开 (`_parse_citation_token`)**：支持单序号（如 `[1]`）、逗号列表（如 `[1, 2]`）以及范围引用（如 `[1-3]`）。为防范恶意超大区间（如 `[1-99999999]`）引发内存耗尽 DoS 攻击，通过常量 `MAX_CITATION_SPAN = 1000` 强制限制区间跨度 `end - start <= 1000`。
- **去重升序输出 (`extract_cited_indices`)**：返回正文中所有有效唯一正整数引用序号的升序列表。

### 3.2 确定性集合比对评估器 (`checker/evaluator.py`)
在 [packages/research-citation/src/research_citation/checker/evaluator.py](../../packages/research-citation/src/research_citation/checker/evaluator.py) 中，`evaluate_references(manuscript_text, bibliography)` 执行集合比对：
- 若未提取到任何有效数字引用，直接返回 `status=CheckStatus.NOT_APPLICABLE`。
- **`IssueKind.OUT_OF_RANGE`**：正文中引用的序号不在有效范围 `range(1, len(bibliography) + 1)` 内。
- **`IssueKind.UNCITED`**：参考文献列表中的条目序号从未在正文引用集合中出现。
- 若存在异常返回 `CheckStatus.ISSUES`，完全匹配则返回 `CheckStatus.PASSED`。

### 3.3 LangChain 工具与 `ResearchCitationToolkit` (`checker/tools.py`)
在 [packages/research-citation/src/research_citation/checker/tools.py](../../packages/research-citation/src/research_citation/checker/tools.py) 中：
- `scan_numeric`（注册名为 `research_citations_scan_numeric`）绑定 `args_schema=ReferenceCheckInput`，调用 `evaluate_references` 并返回 `.model_dump_json()`。
- `ResearchCitationToolkit` 继承自 LangChain 标准 `BaseToolkit`，通过 `get_tools()` 导出 `[scan_numeric]`。

## 4. Pydantic 输入输出契约 (`models/check.py`)

数据契约在 [packages/research-citation/src/research_citation/models/check.py](../../packages/research-citation/src/research_citation/models/check.py) 中定义，遵循严格的 Fail-Closed 原则：

- **状态与问题类型枚举**：定义 `IssueKind(StrEnum)`（`OUT_OF_RANGE`、`UNCITED`）与 `CheckStatus(StrEnum)`（`PASSED`、`ISSUES`、`NOT_APPLICABLE`）。
- **严格模式与长度保护 (`ReferenceCheckInput`)**：开启 `extra="forbid"` 与 `strict=True`，通过 `ManuscriptStr`（1 至 200,000 字符）和 `BibliographyList`（1 至 10,000 条）设定硬性边界。
- **结构化结果与核验范围界定 (`ReferenceCheckResult`)**：包含 `status`、`cited_indices`、`issues: list[ReferenceIssue]`，并明确默认字段 `verified_scope = "Numeric reference indexes only; not evidence or semantic validity"`，防止大模型将编号匹配误判为科学事实真伪。

## 5. 证据优先的审计报告模型 (`models/audit.py`)

在学术评审中，严禁凭空判定学术不端。在 [packages/research-citation/src/research_citation/models/audit.py](../../packages/research-citation/src/research_citation/models/audit.py) 中，系统设计了强约束的审计结论结构：

- **不可变证据引用 (`EvidenceRef`)**：要求记录文档 ID（`document_id`）、页码（`page >= 1`）、文献 64 位十六进制 SHA-256 哈希值（`source_sha256`）与精确原文摘录（`excerpt`）。
- **结论强制证据约束 (`AuditFinding`)**：状态由 `FindingStatus(StrEnum)` 区分为 `VERIFIED`（已核实）、`SUSPECTED`（存疑）和 `INSUFFICIENT_EVIDENCE`（证据不足）。模型后置校验器 `require_evidence_for_verification` 做出强制约束：**当 `self.status == FindingStatus.VERIFIED` 时，`self.evidence` 列表必须非空，否则抛出 `ValueError`**。
- **客观局限性呈现 (`AuditReport`)**：除手稿 ID 与 `findings` 列表外，提供 `limitations: list[str]` 记录客观局限性（例如封闭文献无法获取全文），杜绝主观臆断。

## 相关页面

- [插件机制与贡献契约](../architecture/plugin-system.md)：了解插件在宿主系统中的发现与契约约束。
- [数字引用审计端到端流程](../workflows/citation-audit.md)：查看从 CLI 发起到工具调用的完整工作流。
