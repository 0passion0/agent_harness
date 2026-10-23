"""文本替换与模糊对齐编辑工具模块，支持排版符号归一化与区域换行符保持。"""

from __future__ import annotations

import re
from array import array
from bisect import bisect_left

_SPACE = "\u0009\u000b\u000c\u0020\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff\n\r"
_TRAILING = re.compile(f"[{_SPACE}]+$")
_TYPOGRAPHY = str.maketrans(
    {
        **dict.fromkeys("‘’‚‛", "'"),
        **dict.fromkeys("“”„‟", '"'),
        **dict.fromkeys("‐‑‒–—―−", "-"),
        **dict.fromkeys(
            "\u00a0\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000", " "
        ),
    }
)


class TextEditor:
    """高鲁棒性文本精准/模糊替换器，具备多层级匹配、排版容错与局部换行符继承能力。

    :author: tongliga
    :date: 2026-10-21
    """

    @staticmethod
    def _key(line: str) -> str:
        """去除行尾空白字符并应用常见引号、破折号及全角空格排版字符归一化。

        :param line: 原始文本行。
        :type line: str
        :return: 归一化后的比较键。
        :rtype: str
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：剥离行尾空白并映射特殊排版符号
        return _TRAILING.sub("", line).translate(_TYPOGRAPHY)

    @staticmethod
    def _occurrences(content: str, search: str) -> list[tuple[int, int]]:
        """在源文本中寻找所有完全非重叠出现的子串起止偏移量。

        :param content: 待检索的完整源文本。
        :type content: str
        :param search: 待寻找的目标子串。
        :type search: str
        :return: 包含起止字符偏移量元组的列表。
        :rtype: list[tuple[int, int]]
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：循环 find 寻找不重叠匹配区间
        matches = []
        offset = 0
        while (offset := content.find(search, offset)) != -1:
            matches.append((offset, offset + len(search)))
            offset += len(search)
        return matches

    @staticmethod
    def _line_occurrences(content: str, search: str) -> list[tuple[int, int]]:
        """基于行归一化键对齐的多行模糊匹配，忽略行尾空白与细微排版差异。

        :param content: 源文本内容。
        :type content: str
        :param search: 待查找的多行文本。
        :type search: str
        :return: 源文本中匹配区域的起止字符偏移量列表。
        :rtype: list[tuple[int, int]]
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：分析搜索词的尾部换行特征与预期行序列
        trailing = search.endswith("\n")
        expected = search.split("\n")
        if trailing:
            expected.pop()
        lines = [match for match in re.finditer(r"[^\n]*(?:\n|$)", content) if match[0]]
        result: list[tuple[int, int]] = []

        # 第二步：滑动窗口逐行比较归一化键
        for index, line in enumerate(lines):
            actual = lines[index : index + len(expected)]
            if len(actual) != len(expected):
                continue
            if any(
                TextEditor._key(item[0].removesuffix("\n")) != TextEditor._key(want)
                for item, want in zip(actual, expected, strict=True)
            ):
                continue
            last = actual[-1]
            if trailing and not last[0].endswith("\n"):
                continue
            end = (
                last.end()
                if trailing
                else last.end() - len(last[0]) + len(last[0].removesuffix("\n").removesuffix("\r"))
            )
            # 第三步：防止多处重叠匹配区间污染
            if result and result[-1][1] > line.start():
                continue
            result.append((line.start(), end))
        return result

    @staticmethod
    def _line_ending(text: str, fallback: str) -> str:
        """统计文本中占优的主换行符（CRLF 或 LF），在持平时使用兜底换行符。

        :param text: 输入分析文本。
        :type text: str
        :param fallback: 持平或无换行符时的回退换行符。
        :type fallback: str
        :return: 判定的换行符字符串。
        :rtype: str
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：分别统计 CRLF 与单一 LF 的出现次数
        crlf = text.count("\r\n")
        lf = text.count("\n") - crlf
        # 第二步：按占优多数决定返回换行符
        return fallback if crlf == lf else "\r\n" if crlf > lf else "\n"

    @staticmethod
    def _align_lines(before: list[str], after: list[str]) -> list[int | None]:
        """基于最长公共子序列（LCS）算法对齐替换前后行，映射在新文本中继承的原行号索引。

        :param before: 替换前各行列表。
        :type before: list[str]
        :param after: 替换后各行列表。
        :type after: list[str]
        :return: 长度等于 after 的列表，记录对应 before 的行索引或 None。
        :rtype: list[int | None]
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：贪婪消除相同的前缀行
        prefix = 0
        while prefix < min(len(before), len(after)) and before[prefix] == after[prefix]:
            prefix += 1
        if prefix:
            return list(range(prefix)) + [
                index + prefix if index is not None else None
                for index in TextEditor._align_lines(before[prefix:], after[prefix:])
            ]

        # 第二步：安全消除无重叠冲突的相同后缀行
        suffix = 0
        while suffix < min(len(before), len(after)) and before[-suffix - 1] == after[-suffix - 1]:
            suffix += 1
        if suffix:
            shared = set(before[-suffix:])
            if shared.isdisjoint(before[:-suffix]) and shared.isdisjoint(after[:-suffix]):
                return TextEditor._align_lines(before[:-suffix], after[:-suffix]) + list(
                    range(len(before) - suffix, len(before))
                )

        # 第三步：构建动态规划矩阵求解 LCS
        width = len(after) + 1
        table = array("I", [0]) * ((len(before) + 1) * width)
        for i in range(len(before) - 1, -1, -1):
            row = i * width
            next_row = row + width
            line = before[i]
            for j in range(len(after) - 1, -1, -1):
                if line == after[j]:
                    table[row + j] = table[next_row + j + 1] + 1
                else:
                    down = table[next_row + j]
                    right = table[row + j + 1]
                    table[row + j] = down if down >= right else right

        # 第四步：回溯并填充对齐映射结果
        result: list[int | None] = [None] * len(after)
        removed: list[int] = []
        added: list[int] = []

        i = j = 0
        while i < len(before) and j < len(after):
            if before[i] == after[j]:
                for old, new in zip(removed, added, strict=False):
                    result[new] = old
                removed.clear()
                added.clear()
                result[j] = i
                i += 1
                j += 1
            elif table[(i + 1) * width + j] >= table[i * width + j + 1]:
                removed.append(i)
                i += 1
            else:
                added.append(j)
                j += 1
        removed.extend(range(i, len(before)))
        added.extend(range(j, len(after)))
        for old, new in zip(removed, added, strict=False):
            result[new] = old
        return result

    @staticmethod
    def _region_endings(region: str, replacement: str, fallback: str) -> str:
        """根据原匹配区域的真实换行符序列，为新文本对应行赋予正确的换行终止符。

        :param region: 原始文件中被替换的切片区域。
        :type region: str
        :param replacement: 待插入的新文本内容。
        :type replacement: str
        :param fallback: 无法对齐匹配时的兜底换行符。
        :type fallback: str
        :return: 已正确注入换行符的新文本字符串。
        :rtype: str
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：拆分原区域与新文本行切片
        before = re.split(r"\r?\n", region)
        endings = re.findall(r"\r?\n", region)
        after = replacement.split("\n")

        # 第二步：执行行对齐映射并逐行恢复换行符
        source = TextEditor._align_lines(
            [TextEditor._key(line) for line in before],
            [TextEditor._key(line) for line in after],
        )
        return "".join(
            line
            + (
                endings[source[index]]
                if source[index] is not None and source[index] < len(endings)
                else fallback
            )
            if index < len(after) - 1
            else line
            for index, line in enumerate(after)
        )

    @staticmethod
    def replace_text(content: str, old: str, new: str, replace_all: bool = False) -> str:
        """执行文本查找与替换操作，直接返回替换后的最终文本。

        :param content: 原始文本内容。
        :type content: str
        :param old: 待替换的原始目标片段。
        :type old: str
        :param new: 替换的目标新片段。
        :type new: str
        :param replace_all: 是否全量替换所有出现位置（默认 False 仅允许唯一定位）。
        :type replace_all: bool
        :return: 替换完成后的文本内容。
        :rtype: str
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：调用底层计数替换方法并返回结果文本
        return TextEditor.replace_text_with_count(content, old, new, replace_all)[0]

    @staticmethod
    def replace_text_with_count(
        content: str, old: str, new: str, replace_all: bool = False
    ) -> tuple[str, int]:
        """执行多级层级容错匹配文本替换，并返回替换文本及匹配命中次数。

        支持分级检索：
        1. 精确字符串匹配；
        2. Unicode 排版字符容错匹配；
        3. 忽略行尾空白的多行模糊匹配。
        支持局部继承原文件 CRLF / LF 换行风格与 UTF-8 BOM 头。

        :param content: 待操作的原始文件正文。
        :type content: str
        :param old: 期望被替换的原字符串。
        :type old: str
        :param new: 替换产生的新字符串。
        :type new: str
        :param replace_all: 是否替换全部匹配项。若为 False 且匹配数大于 1 则拒绝执行以防歧义。
        :type replace_all: bool
        :return: (替换后文本, 匹配应用次数) 元组。
        :rtype: tuple[str, int]
        :raises ValueError: 当参数非法、找不到匹配、存在歧义多处匹配或替换前后无变化时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：入参合法性断言
        if old == new:
            raise ValueError("No changes to apply: oldString and newString are identical.")
        if old == "":
            raise ValueError(
                "oldString must not be empty. Use write to create or overwrite a file."
            )

        # 第二步：剥离 BOM 准备分析
        source = content.lstrip("\ufeff")
        endings_only = old.replace("\r", "") == new.replace("\r", "")

        # 第三步：分级定位匹配区间并映射回原字节/字符偏移
        if endings_only:
            matches = TextEditor._occurrences(source, old)
        else:
            view = source.replace("\r\n", "\n")
            search = old.replace("\r\n", "\n")
            replacement = new.replace("\r\n", "\n")
            matches = (
                TextEditor._occurrences(view, search)
                or TextEditor._occurrences(
                    view.translate(_TYPOGRAPHY), search.translate(_TYPOGRAPHY)
                )
                or TextEditor._line_occurrences(view, search)
            )
            removed = [
                match.start() - index for index, match in enumerate(re.finditer("\r\n", source))
            ]
            matches = [
                (start + bisect_left(removed, start), end + bisect_left(removed, end))
                for start, end in matches
            ]

        # 第四步：匹配结果数量检验与歧义阻断
        if not matches:
            raise ValueError(
                "Could not find oldString. It must match exactly, including whitespace and indentation."
            )
        if len(matches) > 1 and not replace_all:
            raise ValueError(
                f"Found {len(matches)} matches for oldString, but expected exactly one. Add more surrounding context to make oldString unique, or set replaceAll to true to replace every occurrence."
            )

        # 第五步：逆序应用文本切片替换，保留原区域换行风格
        result = source
        for start, end in reversed(matches if replace_all else matches[:1]):
            region = source[start:end]
            next_text = (
                new
                if endings_only
                else TextEditor._region_endings(
                    region,
                    replacement,
                    TextEditor._line_ending(region, TextEditor._line_ending(source, "\n")),
                )
            )
            result = result[:start] + next_text + result[end:]

        # 第六步：校验替换实质变更与 BOM 保留
        if result == source:
            raise ValueError(
                "No changes to apply: already has those line endings."
                if endings_only
                else "No changes to apply: the edit leaves the file unchanged. Line endings follow the file; to change them, write \\r explicitly in oldString or newString."
            )
        return (
            ("\ufeff" if content.startswith("\ufeff") or result.startswith("\ufeff") else "")
            + result.lstrip("\ufeff"),
            len(matches),
        )
