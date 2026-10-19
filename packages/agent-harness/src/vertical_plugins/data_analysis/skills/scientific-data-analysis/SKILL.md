---
name: data-analysis-scientific-data-analysis
description: 科研数据分析全流程、表格全量扫描画像、真实脚本执行凭证与 7 大中文章节报告核验。基于 DataAnalysisWorkflow 类封装、静态确定性 LangGraph 固定拓扑流程、节点内 create_agent 机制与 Supervisor 节点控制 Hook。
metadata:
  include_tools: data_analysis_get_node_prompt, data_analysis_inspect_table, data_analysis_ask_researcher, data_analysis_finalize_report
---

# 科研数据分析技能规范 (Scientific Data Analysis Skill)

本技能为科研数据分析插件的**全局指导规范**。本插件采用**完全基于 LangGraph 的确定性固定工作流**（由 `DataAnalysisWorkflow` 统一封装），由主智能体（Supervisor）统一负责任务派发、执行监控、状态参数微调与分支控制，图内特定节点支持调用 `create_agent` 装配专用子智能体。

---

## 1. 核心定位与设计原则

1. **确定性全流程管道**：涵盖参数解析、研究设计澄清、全表 DuckDB 画像、真实科学计算脚本与指标生成、证据哈希审计、7 大段落报告草拟与原子发布。
2. **静态固定拓扑约束**：工作流图拓扑结构严格预定义并预编译，**严禁主智能体实时动态生成图**。主智能体仅可通过调整输入输出参数、调用 Hook 修改节点状态（`modify_node_state`）、节点重试（`retry_node`）或检查点分叉（`fork_node_branch`）进行非破坏性干预。
3. **节点内智能体协作（create_agent）**：在草拟报告、证据核查等特定节点内，允许按需调用 `create_agent` 实例化专用轻量智能体处理任务，无模型时确定性安全回退。
4. **真实凭据闭环（No Hallucination）**：所有分析结论均须由真实执行生成的 Python 脚本（`scripts/*.py`）与指标结果（`artifacts/*.json`）支撑，并由 SHA256 哈希防篡改锁定。

---

## 2. 固定工作流管道 (Fixed 8-Stage Workflow Pipeline)

数据分析工作流包含以下固定流转节点与确定性分支：

```
                    ┌────────────────────────┐
                    │        [START]         │
                    └───────────┬────────────┘
                                │
                                ▼
                ┌──────────────────────────────────┐
                │ 1. parse_and_validate_input      │
                │    (输入解析与 Pydantic 强校验)    │
                └────────────────┬─────────────────┘
                                 │
     ┌───────────────────────────┼───────────────────────────┐
     │ (task_type=profile)       │ (默认/full_analysis)       │ (task_type=validate_report)
     ▼                           ▼                           ▼
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│ 3. profile_table │    │2.clarify_research│    │7.validate_report │
│(DuckDB全表扫描画像)│   │ (规范指南与设计澄清) │   │ (7大段落与SHA256) │
└────────┬─────────┘    └────────┬─────────┘    └────────┬─────────┘
         │                       │                       │
         │                       ▼                       │
         │              ┌──────────────────┐             │
         │              │ 3. profile_table │             │
         │              └────────┬─────────┘             │
         │                       │                       │
         │                       ▼                       │
         │              ┌──────────────────┐             │
         │              │4.execute_analysis│             │
         │              │(真实脚本与指标凭证)│             │
         │              └────────┬─────────┘             │
         │                       │                       │
         │                       ▼                       │
         │              ┌──────────────────┐             │
         │              │5. audit_evidence │             │
         │              │(独立哈希与防漏审计)│             │
         │              └────────┬─────────┘             │
         │                       │                       │
         │                       ▼                       │
         │              ┌──────────────────┐             │
         │              │ 6. draft_report  │             │
         │              │ (草拟7段落标准草稿)│             │
         │              └────────┬─────────┘             │
         │                       │                       │
         │                       ▼                       │
         │              ┌──────────────────┐             │
         │              │7.validate_report │◄────────────┘
         │              └────────┬─────────┘
         │                       │
         └──────────────────────►▼
                ┌──────────────────────────────────┐
                │ 8. finalize_output               │
                │    (output_schema 契约响应组装)   │
                └────────────────┬─────────────────┘
                                 │
                                 ▼
                     ┌────────────────────────┐
                     │         [END]          │
                     └────────────────────────┘
```

### 节点执行规范：
- **`parse_and_validate_input`**：强类型解析输入参数（`file_path`, `sheet_name`, `column_offset`, `markdown_report`, `evidence_files`, `research_question`），非法参数 Fail-Fast 报错。
- **`clarify_research`**：读取科学指南（如 `research-analysis`），必要时通过 `ask_researcher` 澄清研究语义。
- **`profile_table`**：利用 DuckDB 执行全表全行扫描，按列分页返回具有物理边界的列画像，禁止整表倾倒。
- **`execute_analysis`**：生成或执行 Python 科学计算脚本，输出指标 JSON，记录 SHA256 执行凭证。
- **`audit_evidence`**：独立审查脚本退出码与产物哈希，防止虚假数据与数据泄漏。
- **`draft_report`**：生成符合 7 个必需中文二级标题的研究报告草稿，并显式链接真实凭据文件。
- **`validate_report`**：严格核验报告 7 大二级标题与 SHA256 凭证文件，通过后原子落盘发布至 `artifacts/report.md`。
- **`finalize_output`**：将各节点产物封装为标准的 `structured_response` 字典与监控响应消息。

---

## 3. 节点内子任务智能体 (create_agent)

在需要自然语言生成、深度代码审计或复杂推断的节点内，可调用 `DataAnalysisWorkflow.create_node_agent`：
```python
agent = workflow.create_node_agent(
    "draft_report",
    model=model,
    tools=toolkit.get_tools(),
)
if agent is not None:
    res = agent.invoke({"messages": [HumanMessage(content="请撰写分析报告草稿")]})
```
若未配置模型，系统自动切换至确定性模板与算法逻辑，保障单元测试和离线运行的高可靠性。

---

## 4. 节点控制与操作 Hooks (Supervisor Workflow Hooks)

主智能体在监控或处理异常时，禁止重构工作流图，应通过 `vertical_plugins.data_analysis.runtime.hooks` 模块进行无侵入控制：

- **审查检查点**：`inspect_node_checkpoints(graph, thread_id)`
- **修改节点状态**：`modify_node_state(graph, thread_id, values={"column_offset": 20}, as_node="profile_table")`
- **重试节点**：`retry_node(graph, thread_id, checkpoint_id="chk-1", values={...})`
- **分支派生**：`fork_node_branch(graph, thread_id, fork_thread_id="exp-branch-1")`

---

## 5. 交付纪律与约束

- **严禁虚构**：严禁伪造统计数字，必须以 DuckDB 画像或实际 Python 脚本执行结果为准。
- **Fail-Fast**：输入参数缺失或结构不合法时有错必爆，拒绝静默降级或吞咽异常。
- **精简整洁**：遵循极简设计（Ponytail 原则），杜绝不必要的复杂抽象与冗余文件。
