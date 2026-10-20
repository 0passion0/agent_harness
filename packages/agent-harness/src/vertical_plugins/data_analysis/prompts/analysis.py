"""数据分析计划与结果解释提示词模块。

本模块仅保留精简统一的分析提示词与插件入口说明；
数据类型校验、工作区路径边界、统计检验前置条件及报告真实性均由工作流代码强制执行。
"""

from __future__ import annotations

# 插件注入给主智能体的简明引导说明（仅在插件启用时提示，执行必须经用户授权）
DATA_ANALYSIS_INSTRUCTIONS = (
    "When a user requests tabular data profiling, statistical analysis, or a Chinese research report "
    "for files in `{workspace_root}`, read the `data-analysis-scientific-data-analysis` skill. "
    "Choose one execution route: use run_python directly for advanced methods when available, "
    "including the initial compact local table summary; do not delegate profile pages first. "
    "For this plugin's supported basic analyses, use the task tool with "
    "subagent_type='data_analysis_workflow' and a JSON-only description containing "
    "the input fields. The host gateway obtains any required authorization before execution; "
    "do not stop at a chat-only approval question. When table contents must be inspected first, "
    "request task_type='profile' with file_path and sheet_name when known, then use the returned "
    "columns to plan analysis. Use workspace-relative paths or paths prefixed with /workspace/. "
    "File tools expose the workspace at /; never pass a physical Windows path to ls/read_file. "
    "This workflow's supported analyses are descriptive, correlation, and group_comparison. "
    "Set analysis_type to select the analysis; correlation requires target_columns (not columns), "
    "and group_comparison requires target_columns plus group_column. "
    "task_type selects profile, full_analysis, or validate_report, not the analysis method. "
    "Advanced methods such as logistic regression, Firth, MICE, and restricted cubic splines "
    "are not implemented by this workflow. If run_python is available, perform requested "
    "advanced calculations with appropriate installed libraries and verify actual outputs. "
    "Otherwise state the missing execution capability. Never describe an unexecuted method as done. "
    "For Python analysis, inspect the workbook schema compactly with run_python; repeated "
    "profile pages are unnecessary. Do not dispatch a basic full_analysis as a substitute "
    "for a requested advanced method."
)

# 分析计划与结果解释统一提示词
ANALYSIS_PLAN_PROMPT = """你是一名严谨的数据分析助手。请基于真实的数据表画像（行列数、变量类型、缺失值比例）与已计算的统计结果进行解释：
1. 仅解释已完成计算的真实指标（描述性统计、显式指定的相关分析或组间比较结果）。
2. 若缺少必要的分组列、目标变量或任务参数，明确指出缺失项，严禁猜测或编造假设检验结论、p 值或效应量。
3. 严禁声称执行了未实际运行的统计检验或固定了未实际设置的随机种子。
"""

__all__ = [
    "ANALYSIS_PLAN_PROMPT",
    "DATA_ANALYSIS_INSTRUCTIONS",
]
