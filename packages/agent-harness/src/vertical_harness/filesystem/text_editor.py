"""文本替换与模糊对齐编辑工具模块，支持排版符号归一化与区域换行符保持。

本模块提供高精度文本替换引擎 `TextEditor`，负责：
1. 阶梯式精准与模糊匹配查找（字符完全一致 -> Unicode 排版字符映射归一化 -> 忽略行尾空白的多行模糊比对）；
2. 跨平台换行符（CRLF / LF）精细化继承与维护：通过最长公共子序列（LCS）算法建立替换前后行的映射关系，
   确保更新行自动继承被替换区域所在文件的原始换行符风格，避免混用换行符引发 Git 脏 Diff；
3. 支持单处唯一匹配安全替换与 `replace_all=True` 全量批量替换；
4. 自动保留文件开头的 UTF-8 BOM 签名。
"""

from __future__ import annotations

import re
from array import array
from bisect import bisect_left

# 包含各类 Unicode 扩展空白与不可见控制字符的集合
_SPACE: str = (
    "\u0009\u000b\u000c\u0020\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff\n\r"
)
# 匹配行尾不可见空白字符的正则表达式
_TRAILING: re.Pattern[str] = re.compile(f"[{_SPACE}]+$")
# 排版字符映射源字典
_TYPOGRAPHY_MAP: dict[str, str | None] = {
    **dict.fromkeys("‘’‚‛", "'"),
    **dict.fromkeys("“”„‟", '"'),
    **dict.fromkeys("‐‑‒–—―−", "-"),
    **dict.fromkeys(
        "\u00a0\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000", " "
    ),
}
# 排版符号容错转换表：将弯引号、特殊破折号及全角/非断空格映射为标准 ASCII 符号
_TYPOGRAPHY: dict[int, str | None] = str.maketrans(_TYPOGRAPHY_MAP)


class TextEditor:
    """高鲁棒性文本精准/模糊替换器，具备多层级匹配、排版容错与局部换行符继承能力。

    纯静态工具类，为 `edit_file` 工具调用提供底层的文本搜索与安全修改能力。
    """

    @staticmethod
    def _key(line: str) -> str:
        """去除行尾空白字符并应用常见引号、破折号及全角空格排版字符归一化。

        Args:
            line (str): 待计算匹配键的原始行文本。

        Returns:
            str: 归一化后的标准键字符串。
        """
        return _TRAILING.sub("", line).translate(_TYPOGRAPHY)

    @staticmethod
    def _occurrences(content: str, search: str) -> list[tuple[int, int]]:
        """在源文本中寻找所有完全非重叠出现的子串起止偏移量。

        Args:
            content (str): 源文本字符串。
            search (str): 待检索的目标子串。

        Returns:
            list[tuple[int, int]]: 包含各匹配项字符切片起止索引的二元组列表 `[(start, end), ...]`。
        """
        matches = []
        offset = 0
        while (offset := content.find(search, offset)) != -1:
            matches.append((offset, offset + len(search)))
            offset += len(search)
        return matches

    @staticmethod
    def _line_occurrences(content: str, search: str) -> list[tuple[int, int]]:
        """基于行归一化键对齐的多行模糊匹配，忽略行尾空白与细微排版差异。

        Args:
            content (str): 源文本字符串。
            search (str): 目标搜索的多行文本块。

        Returns:
            list[tuple[int, int]]: 模糊匹配命中区间的起止物理字符偏移量列表。
        """
        trailing = search.endswith("\n")
        expected = search.split("\n")
        if trailing:
            expected.pop()
        lines = [match for match in re.finditer(r"[^\n]*(?:\n|$)", content) if match[0]]
        result: list[tuple[int, int]] = []
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
            if result and result[-1][1] > line.start():
                continue
            result.append((line.start(), end))
        return result

    @staticmethod
    def _line_ending(text: str, fallback: str) -> str:
        """统计文本中占优的主换行符（CRLF 或 LF），在持平时使用兜底换行符。

        Args:
            text (str): 待统计换行符特征的文本。
            fallback (str): 占优统计持平或无换行符时的兜底字符（如 `"\n"` 或 `"\r\n"`）。

        Returns:
            str: 占优的换行符字符串。
        """
        crlf = text.count("\r\n")
        lf = text.count("\n") - crlf
        return fallback if crlf == lf else "\r\n" if crlf > lf else "\n"

    @staticmethod
    def _align_lines(before: list[str], after: list[str]) -> list[int | None]:
        """基于最长公共子序列（LCS）算法对齐替换前后行，映射在新文本中继承的原行号索引。

        Args:
            before (list[str]): 替换前的行列表。
            after (list[str]): 替换后的行列表。

        Returns:
            list[int | None]: 与 `after` 长度相同的列表，每一项指向 `before` 中对应的原行号下标；
                若为全新插入行则为 None。
        """
        prefix = 0
        while prefix < min(len(before), len(after)) and before[prefix] == after[prefix]:
            prefix += 1
        if prefix:
            return list(range(prefix)) + [
                index + prefix if index is not None else None
                for index in TextEditor._align_lines(before[prefix:], after[prefix:])
            ]
        suffix = 0
        while suffix < min(len(before), len(after)) and before[-suffix - 1] == after[-suffix - 1]:
            suffix += 1
        if suffix:
            shared = set(before[-suffix:])
            if shared.isdisjoint(before[:-suffix]) and shared.isdisjoint(after[:-suffix]):
                return TextEditor._align_lines(before[:-suffix], after[:-suffix]) + list(
                    range(len(before) - suffix, len(before))
                )
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

        Args:
            region (str): 原文件中被命中的被替换文本切片。
            replacement (str): 待写入的新文本模板。
            fallback (str): 无法从原行继承时的默认换行符。

        Returns:
            str: 格式化修正各行换行符后的最终替换内容。
        """
        before = re.split(r"\r?\n", region)
        endings = re.findall(r"\r?\n", region)
        after = replacement.split("\n")
        source = TextEditor._align_lines(
            [TextEditor._key(line) for line in before],
            [TextEditor._key(line) for line in after],
        )
        parts = []
        for index, line in enumerate(after[:-1]):
            source_index = source[index]
            ending = (
                endings[source_index]
                if source_index is not None and source_index < len(endings)
                else fallback
            )
            parts.append(line + ending)
        return "".join([*parts, after[-1]])

    @staticmethod
    def replace_text(content: str, old: str, new: str, replace_all: bool = False) -> str:
        """执行文本查找与替换操作，直接返回替换后的最终文本。

        Args:
            content (str): 源文件完整文本。
            old (str): 待替换的原字符串，必须严格匹配或排版等价。
            new (str): 准备置换的目标新字符串。
            replace_all (bool): 是否替换所有匹配项，默认 False（要求唯一定位）。

        Returns:
            str: 替换完成后的完整文本。

        Raises:
            ValueError: 替换前后内容无实质变化、原串为空、未找到匹配或多处匹配未指明 replace_all 时抛出。
        """
        return TextEditor.replace_text_with_count(content, old, new, replace_all)[0]

    @staticmethod
    def replace_text_with_count(
        content: str, old: str, new: str, replace_all: bool = False
    ) -> tuple[str, int]:
        """执行多级层级容错匹配文本替换，并返回替换文本及匹配命中次数。

        按序执行：
        1. 检查 old 与 new 是否完全相同；
        2. 纯换行符模式或全文精准/排版归一/行对齐三级搜索；
        3. 单处唯一性断言校验；
        4. 局部换行符继承并逆向应用文本替换；
        5. UTF-8 BOM 还原。

        Args:
            content (str): 源文件完整内容字符串。
            old (str): 待搜索替换的原字符串。
            new (str): 替换目标新文本。
            replace_all (bool): 是否全量替换所有出现的位置。

        Returns:
            tuple[str, int]: 包含替换后最新完整文本与命中替换次数的二元组 `(new_content, count)`。

        Raises:
            ValueError: 当输入非法、找不到目标或出现歧义多重匹配时抛出。
        """
        if old == new:
            raise ValueError("No changes to apply: oldString and newString are identical.")
        if old == "":
            raise ValueError(
                "oldString must not be empty. Use write to create or overwrite a file."
            )
        source = content.lstrip("\ufeff")
        endings_only = old.replace("\r", "") == new.replace("\r", "")
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
        if not matches:
            raise ValueError(
                "Could not find oldString. It must match exactly, including whitespace and indentation."
            )
        if len(matches) > 1 and not replace_all:
            raise ValueError(
                f"Found {len(matches)} matches for oldString, but expected exactly one. Add more surrounding context to make oldString unique, or set replaceAll to true to replace every occurrence."
            )
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
