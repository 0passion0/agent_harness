---
name: data-analysis-scientific-data-analysis
description: 科研数据分析、表格全量扫描画像、7大中文章节报告核验与凭据审计。主智能体通过 LangGraph 确定性固定流程图、节点控制 Hook 与双栖工具集统一监控和操作。
metadata:
  include_tools: data_analysis_get_node_prompt, data_analysis_inspect_table, data_analysis_ask_researcher, data_analysis_finalize_report
---

# 科研数据分析技能规范 (Scientific Data Analysis Skill)

本技能为当前插件的**全局唯一指导规范**。本插件采用**完全基于 LangGraph 的确定性固定工作流**（零子智能体架构），由主智能体（Supervisor）统一负责任务派发、执行监控、状态修改与分支控制。

---

## 1. 核心定位与职责

1. **确定性科研流程**：涵盖表格全量扫描画像、数据清洗统计、严谨结论提炼、7 大中文章节报告格式核验以及 SHA256 证据链审计。
2. **主智能体监控驱动**：摒弃混乱的多子智能体提示词碎片，所有业务逻辑均由统一的 LangGraph 状态图（`data_analysis_workflow`）承载，主智能体可通过图调用、双栖工具或节点 Hook 进行精准干预。
3. **真实凭据约束**：杜绝幻觉伪造，所有分析结果均需关联真实的脚本与指标产物（`artifacts/*.json`, `artifacts/*.png`）并通过哈希防篡改校验。

---

## 2. 固定工作流管道 (Fixed Workflow Pipeline)

数据分析工作流包含以下固定流转节点：

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
          ┌─────────────────────┴─────────────────────┐
          │ (条件路由: 根据 validated_input 任务类型)    │
          ▼                                           ▼
┌───────────────────────────┐               ┌───────────────────────────┐
│ 2. profile_table          │               │ 3. validate_report        │
│    (DuckDB/OpenPyXL全表扫描)│               │    (7大中文章节与哈希凭证核验)│
└─────────────┬─────────────┘               └─────────────┬─────────────┘
              │ (若包含待校验报告)                        │
              └───────────────► ┌─────────┘               │
                                ▼                         │
               ┌──────────────────────────────────┐       │
               │ 4. finalize_output               │◄──────┘
               │    (output_schema 契约响应组装)   │
               └────────────────┬─────────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │         [END]          │
                    └────────────────────────┘
```

### 节点执行规范与系统提示词：
- **`parse_and_validate_input`**：强类型解析输入参数（`file_path`, `sheet_name`, `column_offset`, `markdown_report`, `evidence_files`），非法参数直接 Fail-Fast 报错。
- **`profile_table`**：利用 DuckDB 执行全表全行扫描，按列分页返回具有物理边界的列画像（极值、四分位数、均值、缺失率、Top-N 频次），禁止整表倾倒。
- **`validate_report`**：严格核验 Markdown 报告是否包含 7 个中文二级标题（"## 研究问题", "## 数据说明", "## 分析方法", "## 结果", "## 讨论与局限", "## 结论", "## 复现信息"），并核验所有引用凭证文件的 SHA256 哈希值。
- **`finalize_output`**：将各节点产物封装为标准的 `structured_response` 字典与监控响应消息。

---

## 3. 节点控制与操作 Hooks (Supervisor Workflow Hooks)

主智能体在监控或处理异常时，可直接导入并调用 `data_analysis.hooks` 模块提供的第一公民 Hook 函数：

### Hook 1：审查节点检查点 (`inspect_node_checkpoints`)
查看工作流在各节点沉淀的历史快照与待执行节点：
```python
from data_analysis.hooks import inspect_node_checkpoints

summaries = inspect_node_checkpoints(graph, thread_id="analysis-session-1")
for s in summaries:
    print(f"Step {s.step}: Node={s.next_nodes}, CreatedAt={s.created_at}")
```

### Hook 2：重试失败节点 (`retry_node`)
当某节点出现异常（例如临时文件锁定或输入参数微调），可直接针对该检查点重试：
```python
from data_analysis.hooks import retry_node

# 直接从指定检查点重跑，或注入纠正后的参数
result = retry_node(
    graph,
    thread_id="analysis-session-1",
    checkpoint_id="chk-123",
    values={"column_offset": 50},
)
```

### Hook 3：修改节点状态 (`modify_node_state`)
在节点执行前后纠正或覆写状态变量，保证下游节点接收合规数据：
```python
from data_analysis.hooks import modify_node_state

modify_node_state(
    graph,
    thread_id="analysis-session-1",
    values={"validated_input": {"file_path": "corrected_data.csv"}},
    as_node="parse_and_validate_input",
)
```

### Hook 4：新建分支派生探索 (`fork_node_branch`)
从某节点的检查点派生全新分支（Fork），对比不同分析假设而不污染原会话历史：
```python
from data_analysis.hooks import fork_node_branch

# 派生新分支进行对比实验
fork_cfg = fork_node_branch(
    graph,
    thread_id="analysis-session-1",
    fork_thread_id="analysis-session-1-branch-log-transform",
    checkpoint_id="chk-profile-done",
    values={"task_type": "full_analysis"},
)
```

---

## 4. 双栖工具集 (Dual-Use Plugin Toolkit)

本插件提供的工具既可在 LangGraph 工作流图内被节点调用，也可由主智能体直接使用：

1. **`data_analysis_inspect_table`**：全量扫描 CSV/Parquet/XLSX 数据表并返回分页列画像 JSON。
2. **`data_analysis_finalize_report`**：严格核验 7 个二级标题与 SHA256 凭证文件，并通过原子写入发布到 `artifacts/analysis_report.md`。
3. **`data_analysis_ask_researcher`**：发起人机协同（HITL）中断向研究者询问关键语义。
4. **`data_analysis_get_node_prompt`**：主智能体或图节点按需查询任意节点的系统提示词规范文本。

---

## 5. 交付纪律与约束

- **严禁虚构**：严禁伪造统计数字，必须以 DuckDB 画像或实际 Python 脚本执行结果为准。
- **Fail-Fast**：输入参数缺失或结构不合法时有错必爆，拒绝静默降级或吞咽异常。
- **精简整洁**：遵循极简设计（Ponytail 原则），杜绝不必要的子智能体与提示词冗余。
