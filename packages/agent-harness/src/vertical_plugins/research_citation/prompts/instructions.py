"""引用审计插件贡献给主智能体的系统指令（宿主以 `[research.citations] ` 前缀拼装）。"""

# 引导主智能体委派引用核查工作流或使用技能，且仅报告已验证的不一致项
CITATION_INSTRUCTIONS = (
    "Delegate numeric citation checks to the 'citation_audit' workflow or "
    "use numeric-citation-audit skill for bracket-numbered references. "
    "Report only verified inconsistencies."
)

__all__ = ["CITATION_INSTRUCTIONS"]
