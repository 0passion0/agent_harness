---
type: concept
title: 数字引用审计端到端流程
description: 梳理从 CLI 用户输入、SkillsMiddleware 激活技能、门控工具调用到确定性报告输出的完整端到端流程。
tags: [workflow, citation-audit, end-to-end, skills-middleware, tool-gating]
sources:
  - id: openwiki-source-3738efb5862ea362ae17bac1
    resource: repo://packages/agent-harness/src/vertical_harness/cli.py
  - id: openwiki-source-21ddae62bfa0ad804a7ed2b8
    resource: repo://packages/research-citation/src/research_citation/skills/numeric-citation-audit/SKILL.md
generated: { by: "antigravity", at: "2026-10-18T04:37:34.297Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T06:56:09.371Z
---

# 数字引用审计端到端流程

本页面详细梳理 `vertical-agent-harness` 中由 `vertical-plugin-research-citation` 驱动的数字引用核查全生命周期。该流程生动展示了技能按需加载（Progressive Skills）、门控工具动态暴露与证据优先报告的协同运作机制。

## 1. 端到端执行时序

从用户在终端发起任务到获得最终核查结果，完整交互链路如下：

```mermaid
sequenceDiagram
    autonumber
    actor User as 用户 / 上层调用者
    participant CLI as vertical-agent CLI (_invoke_prompt)
    participant Harness as build_agent / AgentComposer
    participant Agent as Deep Agents 运行时
    participant SkillsMW as SkillsMiddleware
    participant Tool as scan_numeric (evaluate_references)

    User->>CLI: vertical-agent "检查正文 [1] 和 [3]；参考文献 A、B"
    CLI->>Harness: 打开 open_store(settings) 并调用 build_agent()
    Harness-->>CLI: 编译出包含 SkillsMiddleware 与门控工具的 Agent
    CLI->>Agent: agent.invoke(messages, config=RunnableConfig, context=RunContext)
    Agent->>SkillsMW: 检索匹配技能
    SkillsMW-->>Agent: 加载 numeric-citation-audit/SKILL.md
    Note over SkillsMW,Agent: 根据 include_tools 动态暴露 research_citations_scan_numeric
    Agent->>Tool: 调用 scan_numeric(manuscript_text, bibliography)
    Tool-->>Agent: 返回 ReferenceCheckResult JSON (含 out_of_range 和 uncited)
    Agent-->>CLI: 结合 SOP 与工具输出生成客观核查报告
    CLI-->>User: 控制台输出格式化结果
```

## 2. 各阶段关键机制分解

### 2.1 启动与运行时上下文注入 (`cli.py`)
在 [packages/agent-harness/src/vertical_harness/cli.py](../../packages/agent-harness/src/vertical_harness/cli.py) 的 `main` 与 `_invoke_prompt` 函数中：
- 用户输入通过 `_build_parser()` 解析后的命令行参数传入。
- `_invoke_prompt` 构造包含 `thread_id` 的 `RunnableConfig` 以及包含 `tenant_id`、`user_id` 和 `task_tier` 的 `RunContext`。
- 系统使用 `open_store(settings)` 打开持久化存储，调用 `build_agent(settings, store=store)`，并在执行 `agent.invoke` 时注入 `config=run_config` 与 `context=run_context`，保证多租户和模型路由策略的上下文对齐。

### 2.2 提示词引导与技能激活
- 插件在 [plugin.py](../../packages/research-citation/src/research_citation/plugin.py) 的 `PluginContribution` 中注入了系统指令：`"Use numeric-citation-audit skill for bracket-numbered references. Report only verified inconsistencies."`。
- 大模型根据系统提示词引导，识别出需要处理方括号数字引用。
- `SkillsMiddleware` 拦截并感知到虚拟 `/skills/research-citations-numeric-citation-audit/SKILL.md` 下的技能文档（源自 [packages/research-citation/src/research_citation/skills/numeric-citation-audit/SKILL.md](../../packages/research-citation/src/research_citation/skills/numeric-citation-audit/SKILL.md)）。

### 2.3 门控工具按需暴露 (`include_tools`)
- 在 `numeric-citation-audit/SKILL.md` 的 YAML frontmatter 中明确声明了：
  ```yaml
  metadata:
    include_tools: research_citations_scan_numeric
  ```
- `SkillsMiddleware` 读取该元数据后，将由 `ResearchCitationToolkit` 导出且初始隐藏的 `research_citations_scan_numeric` 工具动态注入至当前轮次大模型的工具可见列表中，实现了工具的**渐进式暴露**，避免无用工具干扰模型注意力。

### 2.4 确定性算法核查与输出边界 (`checker/`)
- 大模型调用 [checker/tools.py](../../packages/research-citation/src/research_citation/checker/tools.py) 中的 `scan_numeric`，传入拆解后的正文纯文本与参考文献列表。
- 工具内部调用 `evaluate_references` 与 `extract_cited_indices` 执行严格的正则表达式解析与集合边界比对：
  - 提取正文中所有引用的数字标号（如 `[1]` 与 `[3]`）。
  - 针对超出文献列表总数的引用标记 `out_of_range`（如索引 3 超出长度 2）。
  - 针对列表中存在但在正文中未被引用的条目标记 `uncited`（如索引 2 未被引用）。
- 工具返回 `ReferenceCheckResult` JSON 序列化字符串，附带明确的作用域声明：`verified_scope = "Numeric reference indexes only; not evidence or semantic validity"`。

### 2.5 最终报告生成
根据 `SKILL.md` 中规定的操作流程（SOP）：
1. 明确区分已确认的引用标号错误与论文语义层面的真实性。
2. 仅列出问题索引与可操作的修改清单，**严禁凭空断言学术造假或违规**。
3. 若输入为作者-年份引用（Author-Date），主动说明本数字核查工具不适用。

## 相关页面

- [快速开始与任务导航](../quickstart.md)：查看完整的安装与 CLI 运行指令。
- [学术引用审计插件实现](../integrations/citation-plugin.md)：查看 `scan_numeric` 与数据模式的源码实现。
- [测试架构与质量保证](../testing/test-suite.md)：查看针对引用核查工具的单元测试。
