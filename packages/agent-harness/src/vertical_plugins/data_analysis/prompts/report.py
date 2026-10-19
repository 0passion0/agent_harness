"""数据分析中文 Markdown 报告生成提示词模块。"""

from __future__ import annotations

# 推荐的核心中文报告二级章节标题
CORE_REPORT_SECTIONS: tuple[str, ...] = (
    "研究目标",
    "数据概况",
    "分析方法",
    "分析结果",
    "讨论与局限",
    "结论",
)

# 统一中文报告生成提示词
REPORT_GENERATION_PROMPT = """请根据提供的数据表画像（profile_result）、统计计算结果（analysis_result）与审计记录（audit_result），撰写结构清晰的中文 Markdown 分析报告。

必须包含以下二级标题（`## 标题`）：
- `## 研究目标`
- `## 数据概况`
- `## 分析方法`
- `## 分析结果`
- `## 讨论与局限`
- `## 结论`

核心约束：
1. 报告中引用的所有样本量、缺失值、均值、标准差、分位数及检验结果必须严格来自已完成计算的真实产物。
2. 必须使用相对 `artifacts/report.md` 的路径链接分析脚本（`[分析脚本](../scripts/analyze_data.py)`）与结果文件（`[分析结果](analysis_summary.json)`）。
3. 严禁虚构未执行的统计检验、未计算的指标或“随机种子已固定”等未经验证的描述。
"""

def build_report_prompt(
    *,
    research_question: str,
    profile_summary: dict[str, object] | None = None,
    analysis_summary: dict[str, object] | None = None,
    audit_summary: dict[str, object] | None = None,
) -> str:
    """构建中文 Markdown 分析报告生成提示词。

    :param research_question: 研究目标或问题。
    :type research_question: str
    :param profile_summary: 数据画像摘要字典。
    :type profile_summary: dict[str, object] | None
    :param analysis_summary: 统计计算摘要字典。
    :type analysis_summary: dict[str, object] | None
    :param audit_summary: 产物审计摘要字典。
    :type audit_summary: dict[str, object] | None
    :return: 组装后的报告生成提示词字符串。
    :rtype: str
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：拼接统一提示词与当前运行产物摘要
    return (
        f"{REPORT_GENERATION_PROMPT}\n"
        f"研究目标：{research_question}\n"
        f"数据概况：{profile_summary or {}}\n"
        f"统计结果：{analysis_summary or {}}\n"
        f"审计记录：{audit_summary or {}}\n"
    )


__all__ = [
    "CORE_REPORT_SECTIONS",
    "REPORT_GENERATION_PROMPT",
    "build_report_prompt",
]
