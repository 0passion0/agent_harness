"""数据分析计划与结果解释提示词模块。

本模块仅保留精简统一的分析提示词与插件入口说明；
数据类型校验、工作区路径边界、统计检验前置条件及报告真实性均由工作流代码强制执行。
"""

from __future__ import annotations

# 插件注入给主智能体的简明引导说明（仅在插件启用时提示，执行必须经用户授权）
DATA_ANALYSIS_INSTRUCTIONS = (
    "When a user requests tabular data profiling, statistical analysis, or a Chinese research report "
    "for files in `{workspace_root}`, read the `scientific-data-analysis` skill and request user "
    "authorization before invoking `data_analysis_workflow`."
)

# 分析计划与结果解释统一提示词
ANALYSIS_PLAN_PROMPT = """你是一名严谨的数据分析助手。请基于真实的数据表画像（行列数、变量类型、缺失值比例）与已计算的统计结果进行解释：
1. 仅解释已完成计算的真实指标（描述性统计、显式指定的相关分析或组间比较结果）。
2. 若缺少必要的分组列、目标变量或任务参数，明确指出缺失项，严禁猜测或编造假设检验结论、p 值或效应量。
3. 严禁声称执行了未实际运行的统计检验或固定了未实际设置的随机种子。
"""

# 分析结果解释提示词
ANALYSIS_INTERPRETATION_PROMPT = ANALYSIS_PLAN_PROMPT


def build_analysis_plan_prompt(
    *,
    analysis_goal: str,
    analysis_type: str = "descriptive",
    profile_summary: dict[str, object] | None = None,
) -> str:
    """构建数据分析计划与结果解释提示词。

    :param analysis_goal: 分析目标说明。
    :type analysis_goal: str
    :param analysis_type: 分析类型标识。
    :type analysis_type: str
    :param profile_summary: 可选的数据画像摘要。
    :type profile_summary: dict[str, object] | None
    :return: 组装后的提示词字符串。
    :rtype: str
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：拼接分析目标、分析类型与画像概要
    return (
        f"{ANALYSIS_PLAN_PROMPT}\n"
        f"分析目标：{analysis_goal}\n"
        f"分析类型：{analysis_type}\n"
        f"数据画像：{profile_summary or {}}\n"
    )


__all__ = [
    "ANALYSIS_INTERPRETATION_PROMPT",
    "ANALYSIS_PLAN_PROMPT",
    "DATA_ANALYSIS_INSTRUCTIONS",
    "build_analysis_plan_prompt",
]
