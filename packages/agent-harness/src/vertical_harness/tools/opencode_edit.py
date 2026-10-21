"""Text replacement ported from OpenCode 7d3752c (MIT; see THIRD_PARTY_NOTICES)."""

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


class OpenCodeEdit:
    @staticmethod
    def _key(line: str) -> str:
        return _TRAILING.sub("", line).translate(_TYPOGRAPHY)

    @staticmethod
    def _occurrences(content: str, search: str) -> list[tuple[int, int]]:
        matches = []
        offset = 0
        while (offset := content.find(search, offset)) != -1:
            matches.append((offset, offset + len(search)))
            offset += len(search)
        return matches

    @staticmethod
    def _line_occurrences(content: str, search: str) -> list[tuple[int, int]]:
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
                OpenCodeEdit._key(item[0].removesuffix("\n")) != OpenCodeEdit._key(want)
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
        crlf = text.count("\r\n")
        lf = text.count("\n") - crlf
        return fallback if crlf == lf else "\r\n" if crlf > lf else "\n"

    @staticmethod
    def _align_lines(before: list[str], after: list[str]) -> list[int | None]:
        prefix = 0
        while prefix < min(len(before), len(after)) and before[prefix] == after[prefix]:
            prefix += 1
        if prefix:
            # The source walk greedily keeps equal prefix lines, including repeated lines.
            return list(range(prefix)) + [
                index + prefix if index is not None else None
                for index in OpenCodeEdit._align_lines(before[prefix:], after[prefix:])
            ]
        suffix = 0
        while suffix < min(len(before), len(after)) and before[-suffix - 1] == after[-suffix - 1]:
            suffix += 1
        if suffix:
            shared = set(before[-suffix:])
            # Cross-boundary duplicates can change the source walk's LCS tie-breaking.
            if shared.isdisjoint(before[:-suffix]) and shared.isdisjoint(after[:-suffix]):
                return OpenCodeEdit._align_lines(before[:-suffix], after[:-suffix]) + list(
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
        before = re.split(r"\r?\n", region)
        endings = re.findall(r"\r?\n", region)
        after = replacement.split("\n")
        source = OpenCodeEdit._align_lines(
            [OpenCodeEdit._key(line) for line in before],
            [OpenCodeEdit._key(line) for line in after],
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
        return OpenCodeEdit.replace_text_with_count(content, old, new, replace_all)[0]

    @staticmethod
    def replace_text_with_count(
        content: str, old: str, new: str, replace_all: bool = False
    ) -> tuple[str, int]:
        """Replace using OpenCode's ordered matching tiers and regional line endings."""
        if old == new:
            raise ValueError("No changes to apply: oldString and newString are identical.")
        if old == "":
            raise ValueError(
                "oldString must not be empty. Use write to create or overwrite a file."
            )
        source = content.lstrip("\ufeff")
        endings_only = old.replace("\r", "") == new.replace("\r", "")
        if endings_only:
            matches = OpenCodeEdit._occurrences(source, old)
        else:
            view = source.replace("\r\n", "\n")
            search = old.replace("\r\n", "\n")
            replacement = new.replace("\r\n", "\n")
            matches = (
                OpenCodeEdit._occurrences(view, search)
                or OpenCodeEdit._occurrences(
                    view.translate(_TYPOGRAPHY), search.translate(_TYPOGRAPHY)
                )
                or OpenCodeEdit._line_occurrences(view, search)
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
                else OpenCodeEdit._region_endings(
                    region,
                    replacement,
                    OpenCodeEdit._line_ending(region, OpenCodeEdit._line_ending(source, "\n")),
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
