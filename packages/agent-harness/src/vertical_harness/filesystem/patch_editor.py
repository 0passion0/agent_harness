"""多文件补丁解析与块级差异应用工具模块。

本模块提供 `PatchEditor` 补丁解析与应用器，支持：
1. 标准复合补丁协议语法解析（包括 `*** Begin Patch`、`*** Add File`、`*** Delete File`、
   `*** Update File`、`*** Move to` 以及 `@@` 上下文标记和 `*** End Patch`）；
2. 自动化 Heredoc 语法剥离与 Unicode 空白、弯引号排版容错；
3. 基于多层级容错匹配算法（精确匹配 -> 尾部空白忽略 -> 全空白消除 -> 排版符号归一化）查找原文件中的目标更新行；
4. 按序逆向替换更新行切片，保持 BOM 标识并输出最新文件文本。
"""

from __future__ import annotations

import re

from vertical_harness.filesystem.patch_hunk import PatchHunk
from vertical_harness.filesystem.update_chunk import UpdateChunk

# 包含各类 Unicode 扩展空白与不可见控制字符的集合
_SPACE: str = "\u0009\u000b\u000c\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff\n\r"
# 匹配行尾不可见空白字符的正则表达式
_RIGHT: re.Pattern[str] = re.compile(f"[{_SPACE}]+$")
# 排版字符映射源字典
_TYPOGRAPHY_MAP: dict[str, str | None] = {
    **dict.fromkeys("‘’‚‛", "'"),
    **dict.fromkeys("“”„‟", '"'),
    **dict.fromkeys("‐‑‒–—―−", "-"),
    **dict.fromkeys(
        "\u00a0\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000", " "
    ),
}
# 排版符号容错转换表：将弯引号、特殊破折号及不可见空格映射为标准 ASCII 符号
_TYPOGRAPHY: dict[int, str | None] = str.maketrans(_TYPOGRAPHY_MAP)


class PatchEditor:
    """复合补丁解析与基于上下文的代码重写应用器，支持新增、删除与多块更新操作。

    纯静态工具类，负责解析文本补丁并计算文件更新后的最终内容。
    """

    @staticmethod
    def _trim(value: str) -> str:
        """剔除字符串前后所有空白字符与不可见格式化 Unicode 字符。

        Args:
            value (str): 待处理的原始字符串。

        Returns:
            str: 消除前后空白后的纯净文本。
        """
        return value.strip(_SPACE)

    @staticmethod
    def _boundary(line: str) -> bool:
        """判定当前行是否属于分块结束符或下一个文件分块的起始边界行。

        Args:
            line (str): 补丁文本中的某一行。

        Returns:
            bool: 若为补丁块边界标记则返回 True，否则返回 False。
        """
        return line == "*** End Patch" or line.startswith(
            ("*** Add File: ", "*** Delete File: ", "*** Update File: ")
        )

    @staticmethod
    def _invalid(line: str, number: int, reason: str | None = None) -> ValueError:
        """格式化并生成具有明确行号与原因说明的补丁语法错误异常。

        Args:
            line (str): 出错行的文本内容。
            number (int): 出错行的 1-based 行号。
            reason (str | None): 可选的定制错误原因描述。

        Returns:
            ValueError: 组装好的异常实例。
        """
        return ValueError(
            f"Invalid hunk at line {number}: {reason}"
            if reason
            else f"Invalid hunk at line {number}: '{line}' is not a valid hunk header. Valid hunk headers: '*** Add File: {{path}}', '*** Delete File: {{path}}', '*** Update File: {{path}}'"
        )

    @staticmethod
    def parse_patch(patch_text: str) -> list[PatchHunk]:
        """解析标准 Patch 文本协议为结构化 PatchHunk 操作对象列表。

        自动剥离外层的 Shell heredoc 包装，按行扫描 `*** Begin Patch`、各文件的操作声明
        （Add, Delete, Update, Move to）及更新分块，最后验证 `*** End Patch` 结尾。

        Args:
            patch_text (str): 待解析的原始补丁文本。

        Returns:
            list[PatchHunk]: 结构化补丁操作块列表。

        Raises:
            ValueError: 当补丁缺少头尾标记、首行非法或语法不符合协议时抛出。
        """
        text = PatchEditor._trim(patch_text)
        wrapper = re.fullmatch(
            rf"(?:cat[{_SPACE}]+)?<<(['\"]?)([A-Za-z0-9_]+)\1[{_SPACE}]*\n([\s\S]*?)\n\2[{_SPACE}]*",
            text,
        )
        if wrapper:
            text = wrapper[3]
        lines = [line.removesuffix("\r") for line in text.split("\n")]
        if PatchEditor._trim(lines[0]) != "*** Begin Patch":
            raise ValueError("The first line of the patch must be '*** Begin Patch'")
        if len(lines) < 2 or PatchEditor._trim(lines[-1]) != "*** End Patch":
            raise ValueError("The last line of the patch must be '*** End Patch'")

        hunks: list[PatchHunk] = []
        index, end = 1, len(lines) - 1
        while index < end:
            header = PatchEditor._trim(lines[index])
            if (
                index == 1
                and header.startswith("*** Environment ID:")
                and PatchEditor._trim(header[len("*** Environment ID:") :])
            ):
                index += 1
                continue
            operation = next(
                (
                    kind
                    for kind in ("add", "delete", "update")
                    if header.startswith(f"*** {kind.title()} File: ")
                ),
                None,
            )
            if operation is None:
                raise PatchEditor._invalid(header, index + 1)
            path = PatchEditor._trim(header[len(f"*** {operation.title()} File: ") :])
            hunk_index = index
            index += 1
            if operation == "add":
                content = []
                while index < end and not PatchEditor._boundary(PatchEditor._trim(lines[index])):
                    if not lines[index].startswith("+"):
                        line = PatchEditor._trim(lines[index])
                        raise PatchEditor._invalid(
                            line,
                            index + 1,
                            f"Invalid Add File line for '{path}': expected a line starting with '+', got '{line}'",
                        )
                    content.append(lines[index][1:])
                    index += 1
                hunks.append(PatchHunk("add", path, contents="\n".join(content)))
                continue
            if operation == "delete":
                if index < end and not PatchEditor._boundary(PatchEditor._trim(lines[index])):
                    line = PatchEditor._trim(lines[index])
                    reason = (
                        None
                        if line.startswith("*** ")
                        else f"Unexpected line after Delete File '{path}': '{line}'. Delete hunks do not contain body lines"
                    )
                    raise PatchEditor._invalid(line, index + 1, reason)
                hunks.append(PatchHunk("delete", path))
                continue
            while _RIGHT.sub("", lines[index]) == "*** End of File":
                index += 1
            move_path = None
            move = _RIGHT.sub("", lines[index])
            if move == "*** Move to:" or move.startswith("*** Move to: "):
                move_path = PatchEditor._trim(move[len("*** Move to: ") :])
                if not move_path:
                    raise PatchEditor._invalid(
                        PatchEditor._trim(lines[index]),
                        index + 1,
                        f"Move destination for '{path}' must not be empty",
                    )
                index += 1
            chunks, index = PatchEditor._parse_update(lines, index, end, path, hunk_index)
            hunks.append(PatchHunk("update", path, move_path=move_path, chunks=tuple(chunks)))
        return hunks

    @staticmethod
    def _parse_update(
        lines: list[str], index: int, end: int, path: str, hunk: int
    ) -> tuple[list[UpdateChunk], int]:
        """解析单文件 Update 块内部的 @@ 差异行与上下文分段。

        Args:
            lines (list[str]): 补丁所有按行切片的文本。
            index (int): 当前解析游标所在的起始行索引。
            end (int): 补丁终止行索引。
            path (str): 正在处理的目标文件相对路径。
            hunk (int): 该更新块头部的行索引。

        Returns:
            tuple[list[UpdateChunk], int]: 包含解析出的更新块列表与下一个处理位置行索引的二元组。

        Raises:
            ValueError: 当行前缀不是 `' '`、`'+'`、`'-'`，或分块缺失行内容时抛出。
        """
        chunks: list[UpdateChunk] = []
        old: list[str] = []
        new: list[str] = []
        context = None
        started = False
        after_eof = False

        while index < end:
            line = lines[index]
            update_line = _RIGHT.sub("", line)
            marker = update_line == "@@" or update_line.startswith("@@ ")
            if after_eof:
                if update_line == "":
                    index += 1
                    continue
                if marker:
                    after_eof = False
                elif PatchEditor._boundary(update_line):
                    break
                else:
                    raise PatchEditor._invalid(
                        line,
                        index + 1,
                        f"Expected update hunk to start with a @@ context marker, got: '{line}'",
                    )
            if update_line == "*** End of File":
                if started and not old and not new:
                    raise PatchEditor._invalid(
                        update_line, index + 1, "Update hunk does not contain any lines"
                    )
                if started:
                    chunks.append(UpdateChunk(tuple(old), tuple(new), context, True))
                    started = False
                    after_eof = True
                index += 1
                continue
            if PatchEditor._boundary(update_line):
                break
            if marker:
                if started:
                    if not old and not new:
                        raise PatchEditor._invalid(
                            line,
                            index + 1,
                            f"Unexpected line found in update hunk: '{line}'. Every line should start with ' ' (context line), '+' (added line), or '-' (removed line)",
                        )
                    chunks.append(UpdateChunk(tuple(old), tuple(new), context))
                old, new = [], []
                context = None if update_line == "@@" else update_line[3:]
                started = True
                index += 1
                continue
            if not started:
                old, new, context = [], [], None
                started = True
            if line == "" or line.startswith(" "):
                old.append(line[1:] if line else "")
                new.append(line[1:] if line else "")
            elif line.startswith("-"):
                old.append(line[1:])
            elif line.startswith("+"):
                new.append(line[1:])
            else:
                reason = (
                    f"Expected update hunk to start with a @@ context marker, got: '{line}'"
                    if old or new
                    else f"Unexpected line found in update hunk: '{line}'. Every line should start with ' ' (context line), '+' (added line), or '-' (removed line)"
                )
                raise PatchEditor._invalid(line, index + 1, reason)
            index += 1

        if not chunks and not started:
            raise PatchEditor._invalid(
                PatchEditor._trim(lines[hunk]),
                hunk + 1,
                f"Update file hunk for path '{path}' is empty",
            )
        if started:
            if not old and not new:
                line = PatchEditor._trim(lines[index])
                reason = (
                    "Update hunk does not contain any lines"
                    if line == "*** End Patch"
                    else f"Unexpected line found in update hunk: '{line}'. Every line should start with ' ' (context line), '+' (added line), or '-' (removed line)"
                )
                raise PatchEditor._invalid(line, index + 1, reason)
            chunks.append(UpdateChunk(tuple(old), tuple(new), context))
        return chunks, index

    @staticmethod
    def _seek(lines: list[str], pattern: tuple[str, ...], start: int, eof: bool = False) -> int:
        """在行列表中进行多层级容错匹配查找模式行出现位置。

        依次尝试四种匹配严格程度：
        1. 原文字符完全一致；
        2. 忽略行尾空白；
        3. 消除行首尾所有 Unicode 空白；
        4. 经过排版符号（弯引号、连字符）归一化后匹配。

        Args:
            lines (list[str]): 源文件切分后的文本行列表。
            pattern (tuple[str, ...]): 待查找的目标连续文本行切片。
            start (int): 查找的起始行偏移下标。
            eof (bool): 是否限制必须严格匹配在文件尾部。

        Returns:
            int: 匹配命中的起始行下标；若未找到则返回 -1。
        """
        if not pattern:
            return -1
        offsets = (
            range(len(lines) - len(pattern), len(lines) - len(pattern) + 1)
            if eof
            else range(start, len(lines) - len(pattern) + 1)
        )
        for transform in (
            lambda value: value,
            lambda value: value.rstrip(_SPACE),
            PatchEditor._trim,
            lambda value: PatchEditor._trim(value).translate(_TYPOGRAPHY),
        ):
            expected = tuple(transform(line) for line in pattern)
            for offset in offsets:
                if offset < start or transform(lines[offset]) != expected[0]:
                    continue
                for index in range(1, len(expected)):
                    if transform(lines[offset + index]) != expected[index]:
                        break
                else:
                    return offset
        return -1

    @staticmethod
    def derive_update(path: str, chunks: tuple[UpdateChunk, ...], original: str) -> str:
        """根据原文件正文应用所有 UpdateChunk 块，生成修改后的最新文件文本。

        Args:
            path (str): 目标文件路径，用于在报错时输出友好提示。
            chunks (tuple[UpdateChunk, ...]): 该文件包含的全部更新块元组。
            original (str): 原始文件的完整纯文本字符串。

        Returns:
            str: 应用所有差异替换后生成的最终完整文件内容（保留原有 UTF-8 BOM 标记）。

        Raises:
            ValueError: 当上下文标记未找到，或待替换的原内容在原文件中无法定位时抛出。
        """
        source = original.lstrip("\ufeff")
        lines = source.split("\n")
        if lines[-1] == "":
            lines.pop()

        replacements = []
        line_index = 0
        for chunk in chunks:
            if chunk.change_context:
                context = PatchEditor._seek(lines, (chunk.change_context,), line_index)
                if context == -1:
                    raise ValueError(f"Failed to find context '{chunk.change_context}' in {path}")
                line_index = context + 1
            if not chunk.old_lines:
                replacements.append((len(lines), 0, chunk.new_lines))
                continue
            old, new = chunk.old_lines, chunk.new_lines
            found = PatchEditor._seek(lines, old, line_index, chunk.end_of_file)
            if found == -1 and old[-1] == "":
                old = old[:-1]
                if new and new[-1] == "":
                    new = new[:-1]
                found = PatchEditor._seek(lines, old, line_index, chunk.end_of_file)
            if found == -1:
                if all(line == "" for line in chunk.old_lines):
                    expected = (
                        "an expected blank line"
                        if len(chunk.old_lines) == 1
                        else f"{len(chunk.old_lines)} consecutive blank lines"
                    )
                    raise ValueError(f"Failed to find {expected} in {path}")
                raise ValueError(
                    f"Failed to find expected lines in {path}:\n" + "\n".join(chunk.old_lines)
                )
            replacements.append((found, len(old), new))
            line_index = found + len(old)
        replacements.sort(key=lambda item: item[0])
        for start, remove, insert in reversed(replacements):
            lines[start : start + remove] = insert
        if not lines or lines[-1] != "":
            lines.append("")
        result = "\n".join(lines)
        return (
            "\ufeff" if original.startswith("\ufeff") or result.startswith("\ufeff") else ""
        ) + result.lstrip("\ufeff")
