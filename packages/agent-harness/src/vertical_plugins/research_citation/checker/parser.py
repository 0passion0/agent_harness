"""纯函数式方括号数字引用解析器模块。"""

from __future__ import annotations

import logging
import re
from itertools import chain

# 正则表达式：用于匹配方括号包围的数字引用组合，例如 [1]、[1, 2]、[1-3]
_BRACKET_PATTERN = re.compile(r"\[([0-9,\s\-]+)\]")
# 单个逗号分隔引用标记正则：支持单个正整数 "1" 或闭区间范围 "1-3"
_TOKEN_PATTERN = re.compile(r"^(\d+)(?:\s*-\s*(\d+))?$", re.ASCII)

# 区间引用允许的最大跨度阈值，防范恶意超大范围（如 [1-99999999]）造成的内存消耗
MAX_CITATION_SPAN = 1000
MAX_CITATION_DIGITS = 9
logger = logging.getLogger(__name__)


def _parse_citation_token(token: str, *, max_span: int = MAX_CITATION_SPAN) -> range:
    """将单个引用标记（如 '2' 或 '3-5'）解析为正整数 range 对象。

    :param token: 去空白后的单个引用标记字符串。
    :type token: str
    :param max_span: 允许的最大区间跨度。
    :type max_span: int
    :return: 包含对应引用序号的 range 对象；格式非法或越界时返回空 range(0)。
    :rtype: range
    :raises ValueError: 当数字引用 ID 字符长度超出 MAX_CITATION_DIGITS（9 位）时抛出。
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：使用正则精确匹配单个正整数或破折号连接的区间范围
    match = _TOKEN_PATTERN.fullmatch(token.strip())
    if match is None:
        logger.debug("citation_token_ignored", extra={"event_fields": {"status": "invalid_format"}})
        return range(0)

    # 第二步：校验数字字符串长度，防止超大数字导致内存消耗或异常溢出
    numbers = [match.group(1), match.group(2) or match.group(1)]
    if any(len(number) > MAX_CITATION_DIGITS for number in numbers):
        raise ValueError(f"Numeric citation IDs must contain at most {MAX_CITATION_DIGITS} digits")

    # 第三步：转换起始与终止数值并校验区间范围与跨度上限
    start, end = map(int, numbers)
    if 0 < start <= end and (end - start) <= max_span:
        return range(start, end + 1)
    logger.debug("citation_token_ignored", extra={"event_fields": {"status": "invalid_range"}})
    return range(0)


def extract_cited_indices(manuscript_text: str) -> list[int]:
    """扫描手稿正文并提取所有去重升序排列的有效方括号数字引用序号。

    :param manuscript_text: 包含诸如 [1]、[1, 2] 或 [1-3] 形式数字引用的手稿正文纯文本。
    :type manuscript_text: str
    :return: 升序排列且已去重的唯一正整数引用序号列表。
    :rtype: list[int]
    :raises ValueError: 当正文中的数字引用包含超长非法数字时抛出。
    :author: tongliga
    :date: 2026-10-19

    .. code-block:: python

        # 示例手稿文本：
        # "正如已有研究所证明 [1, 3-5]，该机制在 [2] 中被进一步扩展。"
        # 输出列表契约：
        [1, 2, 3, 4, 5]
    """
    logger.debug("citation_parsing_started")
    # 第一步：正则扫描手稿中的所有方括号引用块，并按逗号拆分成离散标记片段
    raw_tokens = (
        part
        for bracket_match in _BRACKET_PATTERN.finditer(manuscript_text)
        for part in bracket_match.group(1).split(",")
    )
    # 第二步：将离散标记解析并展开为数字序列生成器
    expanded = chain.from_iterable(_parse_citation_token(token) for token in raw_tokens)
    # 第三步：集合去重并升序排序，输出标准正整数序号列表
    indices = sorted(set(expanded))
    logger.debug(
        "citation_parsing_completed", extra={"event_fields": {"citation_count": len(indices)}}
    )
    return indices


__all__ = [
    "MAX_CITATION_SPAN",
    "extract_cited_indices",
]
