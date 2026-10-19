"""数据分析插件贡献给主智能体的系统指令（宿主以 `[data_analysis] ` 前缀拼装）。"""

# 引导主智能体使用内置分析工具或委派数据分析工作流，且禁止臆造结果
DATA_ANALYSIS_INSTRUCTIONS = (
    "Built-in scientific data analysis and reporting tools are available. "
    "When profiling tabular data or validating reports, use inspect_table, "
    "read_analysis_guide, and finalize_report, or delegate to 'data_analysis_workflow'. "
    "Do not fabricate results."
)

__all__ = ["DATA_ANALYSIS_INSTRUCTIONS"]
