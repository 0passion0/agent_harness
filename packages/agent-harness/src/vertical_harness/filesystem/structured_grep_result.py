"""包含结构化语法与行偏移元数据的搜索结果数据载体模块。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from deepagents.backends.protocol import GrepResult


@dataclass
class StructuredGrepResult(GrepResult):
    """扩展 Grep 结果数据模型，保留行号、字节偏移量与子匹配高亮等结构化元数据。

    :param source_matches: 结构化匹配结果列表，包含条目路径、行号、偏移量及子匹配元数据。
    :type source_matches: list[dict[str, typing.Any]]
    :author: tongliga
    :date: 2026-10-21
    """

    source_matches: list[dict[str, Any]] = field(default_factory=list)
