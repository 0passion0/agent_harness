---
type: concept
title: 学术引用审计插件实现
description: 深入分析 vertical-plugin-research-citation 演示插件的内部架构，涵盖 scan_numeric 核验工具、数据契约、技能资产与证据优先审计模型。
tags: [plugin, research-citation, validation, pydantic, evidence-first]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T03:23:43.651Z
sources:
  - id: openwiki-source-721e3ae67db867a56eacdc71
    resource: repo://packages/research-citation/src/research_citation/plugin.py
  - id: openwiki-source-9345281d0c7186af5365bead
    resource: repo://packages/research-citation/src/research_citation/report.py
  - id: openwiki-source-fcd5aa98ed59cee5fdced2ed
    resource: repo://packages/research-citation/src/research_citation/tools.py
generated: { by: "antigravity", at: "2026-10-18T03:23:43.651Z" }
---

# 学术引用审计插件实现

`vertical-plugin-research-citation` 是基于垂直领域插件标准规范实现的学术手稿引用核查插件。该插件作为独立的 Python distribution（安装包），通过标准入口点接入宿主 Harness，演示了如何在不修改主工程代码的前提下注入确定性算法、Pydantic 契约校验模型与 `SKILL.md` 知识资产。

## 1. 插件结构与构建贡献 (`plugin.py`)

在 [packages/research-citation/src/research_citation/plugin.py](../../packages/research-citation/src/research_citation/plugin.py) 中，`ResearchCitationPlugin` 实现了 `DomainPlugin` 协议：

- **清单声明 (`PluginManifest`)**：插件标识符为 `research.citations`，接口版本为 1，语义化版本为 `0.1.0`。
- **静态技能读取**：通过 `importlib.resources.files` 安全读取打包在 wheel 包内的 `numeric-citation-audit/SKILL.md` 内容。
- **贡献载荷组装 (`build`)**：
  - 工具注册：将 `scan_numeric` 工具配置为门控暴露模式（`Exposure.SKILL`），仅在模型激活对应技能后才暴露该工具。
  - 技能挂载：声明 `SkillAsset(relative_path="numeric-citation-audit/SKILL.md", content=...)`，供宿主挂载至虚拟 `/skills` 目录。
  - 指令注入：向 Agent 提示词追加专属指令，要求其使用 `numeric-citation-audit` 技能并仅汇报核实不一致的问题。

```python
class ResearchCitationPlugin:
    manifest = PluginManifest(
        plugin_id="research.citations",
        api_version=1,
        version="0.1.0",
        description="Deterministic in-text reference checks for research manuscripts",
    )
```

## 2. 确定性引用核查工具 (`tools.py`)

工具 `scan_numeric`（注册名为 `research_citations_scan_numeric`）定义在 [packages/research-citation/src/research_citation/tools.py](../../packages/research-citation/src/research_citation/tools.py) 中，专门针对括号数字引用格式进行纯确定性算法比对：

- **语法正则解析**：通过正则表达式 `\[([0-9,\s\-]+)\]` 提取行内引用标号。
- **混合格式支持**：支持单序号（如 `[1]`）、逗号列表（如 `[1, 2]`）以及范围引用（如 `[1-3]`）。为防止大范围解析 DoS 攻击，针对区间引用限制跨度 `end - start <= 1000`。
- **边界与覆盖率核查**：
  - `out_of_range`：正文中引用的序号超出参考文献列表总长度（`i < 1 or i > len(bibliography)`）。
  - `uncited`：参考文献列表中的条目在正文中从未出现。
- **状态判定与范围界定**：若无任何引用返回 `not_applicable`；若存在异常返回 `issues`；完全匹配返回 `passed`。返回体明确标注 `verified_scope = "Numeric reference indexes only; not evidence or semantic validity"`，防止大模型将格式匹配误解为科学事实真伪。

## 3. Pydantic 输入输出契约 (`schemas.py`)

数据契约在 [packages/research-citation/src/research_citation/schemas.py](../../packages/research-citation/src/research_citation/schemas.py) 中定义，遵循严格的 Fail-Closed 原则：

- **严格模式与长度保护**：`ReferenceCheckInput` 开启 `extra="forbid"` 与 `strict=True`，对手稿字符数（最大 20 万字）和文献条目数（最大 1 万条）设定硬限制。
- **结构化异常描述**：`ReferenceIssue` 规范了异常类别枚举（`out_of_range`、`uncited`）、问题索引数字与文字解释。

## 4. 证据优先的审计报告模型 (`report.py`)

在学术评审中，严禁凭空判定学术不端。在 [packages/research-citation/src/research_citation/report.py](../../packages/research-citation/src/research_citation/report.py) 中，系统设计了强约束的审计结论结构：

- **不可变证据引用 (`EvidenceRef`)**：要求记录文档 ID、页码、文献 SHA-256 哈希值与精确原文摘录。
- **结论强制证据约束 (`AuditFinding`)**：状态区分为 `verified`（已核实）、`suspected`（存疑）和 `insufficient_evidence`（证据不足）。模型校验器 `require_evidence_for_verification` 做出强制约束：**当结论为 `verified` 时，证据列表必须非空，否则抛出 `ValueError`**。
- **客观局限性呈现 (`AuditReport`)**：除报告结论列表外，强制要求列出客观存在的局限性（`limitations`，例如未获取付费数据库全文），杜绝主观臆断。

## 相关页面

- [插件机制与贡献契约](../architecture/plugin-system.md)：了解插件在宿主系统中的发现与契约约束。
- [数字引用审计端到端流程](../workflows/citation-audit.md)：查看从 CLI 发起到工具调用的完整工作流。
