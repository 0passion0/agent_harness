"""Data Analysis 科研数据分析插件包公共导出模块。

架构子包与模块划分：
- `data_analysis.models`: 表格画像、报告校验与执行凭证 Pydantic 强类型模型
- `data_analysis.table_profile`: 基于 DuckDB 与 OpenPyXL 的表格全量行扫描画像算法
- `data_analysis.reports`: 中文科研报告七大核心段落校验与原子发布逻辑
- `data_analysis.evidence`: 记录科学计算脚本与产物 SHA256 哈希的中间件
- `data_analysis.desktop`: 桌面客户端视图映射与状态快照
- `data_analysis.prompts`: 工作流固定节点与原生科研规范指南定义
- `data_analysis.hooks`: 节点重试、状态修改、分支派生与检查点审查 Hook
- `data_analysis.tools`: 面向 LangChain / Deep Agents 的原生双栖工具集与 Toolkit
- `data_analysis.workflow`: 8 阶段确定性全流程 LangGraph 管道与 `DataAnalysisWorkflow` 类封装
- `data_analysis.plugin`: 遵循 `vertical_harness` 领域插件协议的入口点实现
"""

from logging import NullHandler, getLogger

from data_analysis.desktop import DESCRIPTOR, artifact_paths, snapshot
from data_analysis.evidence import AnalysisEvidence, AnalysisState, merge_executions
from data_analysis.hooks import (
    fork_node_branch,
    inspect_node_checkpoints,
    modify_node_state,
    retry_node,
)
from data_analysis.models import (
    AnalysisStateModel,
    ColumnProfile,
    ColumnValueCount,
    ExecutionReceipt,
    NumericSubset,
    ReportValidationInput,
    ReportValidationResult,
    StrictModel,
    TableProfileInput,
    TableProfileResult,
)
from data_analysis.plugin import (
    DATA_ANALYSIS_WORKFLOW_NAME,
    SKILL_RELATIVE_PATH,
    DataAnalysisInputState,
    DataAnalysisInternalState,
    DataAnalysisOutputState,
    DataAnalysisPlugin,
    DataAnalysisWorkflow,
    InternalState,
    build_data_analysis_workflow,
    build_data_analysis_workflow_contribution,
    graph,
    input_schema,
    output_schema,
)
from data_analysis.prompts import (
    AUDIT_EVIDENCE_NODE_PROMPT,
    CLARIFY_RESEARCH_NODE_PROMPT,
    DRAFT_REPORT_NODE_PROMPT,
    EXECUTE_ANALYSIS_NODE_PROMPT,
    FINALIZE_OUTPUT_NODE_PROMPT,
    GUIDE_DESCRIPTIONS,
    INPUT_VALIDATION_NODE_PROMPT,
    NODE_AUDIT_EVIDENCE,
    NODE_CLARIFY_RESEARCH,
    NODE_DRAFT_REPORT,
    NODE_EXECUTE_ANALYSIS,
    NODE_FINALIZE_OUTPUT,
    NODE_INPUT_VALIDATION,
    NODE_PROFILE_TABLE,
    NODE_PROMPTS_MAP,
    NODE_VALIDATE_REPORT,
    PROFILE_NODE_PROMPT,
    VALIDATE_REPORT_NODE_PROMPT,
    GuideName,
    get_node_prompt,
    read_analysis_guide,
)
from data_analysis.reports import REPORT_SECTIONS, save_report, validate_report
from data_analysis.table_profile import (
    PROFILE_CONTEXT_LIMIT,
    PROFILE_PAGE_SIZE,
    profile_table,
)
from data_analysis.tools import DataAnalysisToolkit

getLogger(__name__).addHandler(NullHandler())

__all__ = [
    "AUDIT_EVIDENCE_NODE_PROMPT",
    "CLARIFY_RESEARCH_NODE_PROMPT",
    "DATA_ANALYSIS_WORKFLOW_NAME",
    "DESCRIPTOR",
    "DRAFT_REPORT_NODE_PROMPT",
    "EXECUTE_ANALYSIS_NODE_PROMPT",
    "FINALIZE_OUTPUT_NODE_PROMPT",
    "GUIDE_DESCRIPTIONS",
    "GuideName",
    "INPUT_VALIDATION_NODE_PROMPT",
    "NODE_AUDIT_EVIDENCE",
    "NODE_CLARIFY_RESEARCH",
    "NODE_DRAFT_REPORT",
    "NODE_EXECUTE_ANALYSIS",
    "NODE_FINALIZE_OUTPUT",
    "NODE_INPUT_VALIDATION",
    "NODE_PROFILE_TABLE",
    "NODE_PROMPTS_MAP",
    "NODE_VALIDATE_REPORT",
    "PROFILE_CONTEXT_LIMIT",
    "PROFILE_NODE_PROMPT",
    "PROFILE_PAGE_SIZE",
    "REPORT_SECTIONS",
    "SKILL_RELATIVE_PATH",
    "VALIDATE_REPORT_NODE_PROMPT",
    "AnalysisEvidence",
    "AnalysisState",
    "AnalysisStateModel",
    "ColumnProfile",
    "ColumnValueCount",
    "DataAnalysisInputState",
    "DataAnalysisInternalState",
    "DataAnalysisOutputState",
    "DataAnalysisPlugin",
    "DataAnalysisToolkit",
    "DataAnalysisWorkflow",
    "ExecutionReceipt",
    "InternalState",
    "NumericSubset",
    "ReportValidationInput",
    "ReportValidationResult",
    "StrictModel",
    "TableProfileInput",
    "TableProfileResult",
    "artifact_paths",
    "build_data_analysis_workflow",
    "build_data_analysis_workflow_contribution",
    "fork_node_branch",
    "get_node_prompt",
    "graph",
    "input_schema",
    "inspect_node_checkpoints",
    "merge_executions",
    "modify_node_state",
    "output_schema",
    "profile_table",
    "read_analysis_guide",
    "retry_node",
    "save_report",
    "snapshot",
    "validate_report",
]
