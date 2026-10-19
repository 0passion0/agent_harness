"""科研数据分析精简提示词导出模块。"""

from vertical_plugins.data_analysis.prompts.analysis import (
    ANALYSIS_INTERPRETATION_PROMPT,
    ANALYSIS_PLAN_PROMPT,
    DATA_ANALYSIS_INSTRUCTIONS,
    build_analysis_plan_prompt,
)
from vertical_plugins.data_analysis.prompts.report import (
    CORE_REPORT_SECTIONS,
    REPORT_GENERATION_PROMPT,
    build_report_prompt,
)

__all__ = [
    "ANALYSIS_INTERPRETATION_PROMPT",
    "ANALYSIS_PLAN_PROMPT",
    "CORE_REPORT_SECTIONS",
    "DATA_ANALYSIS_INSTRUCTIONS",
    "REPORT_GENERATION_PROMPT",
    "build_analysis_plan_prompt",
    "build_report_prompt",
]
