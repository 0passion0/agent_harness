"""包含结构化语法与行偏移元数据的搜索结果数据载体模块。

本模块提供 `StructuredGrepResult` 数据类，继承自 DeepAgents 的标准 `GrepResult`。
除了保留用于 LLM 快速消费的纯文本行列表外，还补充了包含物理行号、
子匹配位置与字节偏移量的高保真结构化匹配元数据，支持前端编辑器精确定位与高亮展示。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from deepagents.backends.protocol import GrepResult


@dataclass
class StructuredGrepResult(GrepResult):
    """扩展 Grep 结果数据模型，保留行号、字节偏移量与子匹配高亮等结构化元数据。

    Attributes:
        source_matches (list[dict[str, Any]]): 结构化匹配项列表。
            每个条目是一个包含以下键值的字典：
            - `path` (str): 匹配文件在工作区内的相对路径；
            - `line_number` (int): 匹配行在文件中的 1-based 物理行号；
            - `line_text` (str): 匹配行的完整内容文本；
            - `submatches` (list[dict[str, Any]]): 正则表达式捕获区间，包含 `start` 与 `end` 字符偏移。
    """

    # 包含物理行号、绝对字节偏移量与正则子匹配高亮的结构化匹配条目列表
    source_matches: list[dict[str, Any]] = field(default_factory=list)
