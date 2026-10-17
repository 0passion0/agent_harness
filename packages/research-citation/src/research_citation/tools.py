"""作为 LangChain 工具暴露的确定性学术引用检查实现模块。"""

import re
from langchain.tools import tool

from research_citation.schemas import (
    ReferenceCheckInput,
    ReferenceCheckResult,
    ReferenceIssue,
)

# 正则表达式：用于匹配方括号包围的数字引用组合，例如 [1]、[1, 2]、[1-3]
_BRACKET_PATTERN = re.compile(r"\[([0-9,\s\-]+)\]")


@tool("research_citations_scan_numeric", args_schema=ReferenceCheckInput)
def scan_numeric(manuscript_text: str, bibliography: list[str]) -> str:
    """根据参考文献列表长度校验手稿中的数字引用序号。

    本工具仅对数字引用标号进行范围与覆盖率验证，不验证引用的论文是否存在或是否支持相应学术观点。

    :param manuscript_text: 包含诸如 [1]、[1, 2] 或 [1-3] 形式数字引用的手稿正文纯文本。
    :param bibliography: 按照手稿引用顺序排列的参考文献条目列表。
    :return: 经过 Pydantic 校验并序列化为 JSON 字符串的引用序号异常比对结果。
    """
    # 存储从手稿中解析出的所有被引用的正整数序号
    cited = set()

    # 扫描手稿全文，正则查找所有方括号数字引用片段
    for match in _BRACKET_PATTERN.finditer(manuscript_text):
        content = match.group(1)
        # 支持逗号分隔的多个引用，如 "1, 2, 5-7"
        for part in content.split(","):
            part = part.strip()
            # 处理形如 "1-3" 的区间范围引用
            if "-" in part:
                pieces = [s.strip() for s in part.split("-")]
                # 校验区间两端格式是否为双整数
                if len(pieces) != 2 or not all(s.isdecimal() for s in pieces):
                    continue
                start, end = map(int, pieces)
                # 确保起始索引合法且范围跨度在合理阈值（<=1000）以内，防范超大范围 DoS 消耗
                if 0 < start <= end and end - start <= 1000:
                    cited.update(range(start, end + 1))
            # 处理单数字引用
            elif part.isdecimal():
                cited.add(int(part))

    # 若手稿中完全未找到任何数字引用标号，返回不适用（not_applicable）状态
    if not cited:
        return ReferenceCheckResult(status="not_applicable", cited_indices=[], issues=[]).model_dump_json()

    # 1. 查找超出参考文献列表长度的非法引用（如引了 [5] 但文献列表只有 3 篇）
    issues = [
        ReferenceIssue(kind="out_of_range", index=i, explanation="No bibliography entry at this index")
        for i in sorted(cited)
        if i < 1 or i > len(bibliography)
    ]

    # 2. 查找参考文献列表中存在但在手稿正文中从未被引用的孤立文献条目
    issues.extend(
        ReferenceIssue(kind="uncited", index=i, explanation="Bibliography entry has no matching citation")
        for i in range(1, len(bibliography) + 1)
        if i not in cited
    )

    # 构造最终校验结果模型并序列化为 JSON 字符串返回
    return ReferenceCheckResult(
        status="issues" if issues else "passed",
        cited_indices=sorted(cited),
        issues=issues,
    ).model_dump_json()
