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
3. 严禁虚构未执行的统计检验或未计算的指标。
"""


__all__ = [
    "CORE_REPORT_SECTIONS",
    "REPORT_GENERATION_PROMPT",
]
