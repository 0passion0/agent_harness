"""Bounded text pages adapted from OpenCode, with the backend's UTF-8 contract."""

import base64
import os
import posixpath
import unicodedata
from collections.abc import Callable
from pathlib import Path

from deepagents.backends.protocol import FileData, ReadResult

MAX_READ_LINES = 2000
MAX_READ_BYTES = 50 * 1024
MAX_LINE_LENGTH = 2000
MAX_MEDIA_INGEST_BYTES = 20 * 1024 * 1024


class OpenCodeRead:
    @staticmethod
    def canonical(name: str) -> str:
        return unicodedata.normalize("NFC", name).translate(
            str.maketrans({"\u00a0": " ", "\u202f": " ", "\u2018": "'", "\u2019": "'"})
        )

    @staticmethod
    def read_supported_media(path: Path, *, pdf: bool = False) -> tuple[str, str] | None:
        with path.open("rb") as stream:
            prefix = stream.read(12)
            if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            elif prefix.startswith(b"\xff\xd8\xff"):
                mime = "image/jpeg"
            elif prefix.startswith(b"GIF8"):
                mime = "image/gif"
            elif prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
                mime = "image/webp"
            elif pdf and prefix.startswith(b"%PDF-"):
                mime = "application/pdf"
            else:
                return None
            if os.fstat(stream.fileno()).st_size > MAX_MEDIA_INGEST_BYTES:
                raise ValueError(f"Media exceeds {MAX_MEDIA_INGEST_BYTES} byte limit")
            stream.seek(0)
            content = stream.read(MAX_MEDIA_INGEST_BYTES + 1)
            if len(content) > MAX_MEDIA_INGEST_BYTES:
                raise ValueError(f"Media exceeds {MAX_MEDIA_INGEST_BYTES} byte limit")
        return mime, base64.b64encode(content).decode("ascii")

    @staticmethod
    def recover_read_path(root: Path, requested: Path) -> Path | None:
        base = OpenCodeRead.canonical(requested.name)
        try:
            with os.scandir(requested.parent) as entries:
                matches = [
                    requested.parent / entry.name
                    for entry in entries
                    if entry.is_file(follow_symlinks=False)
                    and OpenCodeRead.canonical(entry.name) == base
                ]
            if len(matches) != 1:
                return None
            candidate = matches[0].resolve()
            return candidate if candidate.is_relative_to(root.resolve()) else None
        except OSError:
            # OpenCode treats alternate-path discovery failure as no alternate path.
            return None

    @staticmethod
    def missing_read_message(
        requested_virtual: str,
        requested: Path,
        authorize: Callable[[str], str | None] | None = None,
    ) -> str:
        base = posixpath.basename(requested_virtual).lower()
        try:
            suggestions = [
                posixpath.join(posixpath.dirname(requested_virtual), entry.name)
                for entry in requested.parent.iterdir()
                if base in entry.name.lower() or entry.name.lower() in base
                if authorize is None
                or not authorize(posixpath.join(posixpath.dirname(requested_virtual), entry.name))
            ][:3]
        except OSError:
            # Suggestion discovery is optional in the source missing-file mechanism.
            suggestions = []
        message = f"File not found: {requested_virtual}"
        if suggestions:
            message += "\n\nDid you mean one of these?\n" + "\n".join(suggestions)
        return message

    @staticmethod
    def directory_entries(path: Path) -> list[tuple[str, bool]]:
        with os.scandir(path) as entries:
            visible = [
                (entry.name, entry.is_dir(follow_symlinks=False))
                for entry in entries
                if entry.is_dir(follow_symlinks=False)
                or entry.is_file(follow_symlinks=False)
                or entry.is_symlink()
            ]
        # Source uses localeCompare; backend paths use deterministic Python lexical ordering.
        return sorted(visible, key=lambda entry: (not entry[1], entry[0]))

    @staticmethod
    def read_text_page(path: Path, offset: int = 0, limit: int = 2000) -> ReadResult:
        offset, limit = max(0, offset), min(MAX_READ_LINES, max(0, limit))
        if not limit:
            return ReadResult(
                file_data=FileData(content="", encoding="utf-8"), no_lines_requested=True
            )
        selected: list[str] = []
        size = 0
        index = 0
        more = False
        with path.open(encoding="utf-8-sig") as stream:
            while True:
                prefix = b""
                length = 0
                binary = False
                ending = ""
                while part := stream.readline(8192):
                    binary = binary or "\0" in part
                    text = part[:-1] if part.endswith("\n") else part
                    units = text.encode("utf-16-le")
                    length += len(units) // 2
                    if len(prefix) < MAX_LINE_LENGTH * 2:
                        prefix += units[: MAX_LINE_LENGTH * 2 - len(prefix)]
                    if part.endswith("\n"):
                        ending = "\n"
                        break
                if not part and not length:
                    break
                if index >= offset and len(selected) >= limit:
                    more = True
                    break
                if binary and index < offset:
                    return ReadResult(error="Binary file: use a suitable Python library.")
                if index >= offset:
                    line = (
                        prefix.decode("utf-16-le", errors="surrogatepass")
                        + ("... (line truncated to 2000 chars)" if length > MAX_LINE_LENGTH else "")
                        + ending
                    )
                    line_size = len(line.encode("utf-8", errors="surrogatepass"))
                    if size + line_size > MAX_READ_BYTES:
                        more = True
                        break
                    if binary:
                        return ReadResult(error="Binary file: use a suitable Python library.")
                    selected.append(line)
                    size += line_size
                index += 1
        if not selected and offset and not more:
            return ReadResult(error=f"Line offset {offset} exceeds file length")
        end = offset + len(selected)
        return ReadResult(
            file_data=FileData(content="".join(selected), encoding="utf-8"),
            start_line=offset + 1 if selected else None,
            end_line=end if selected else None,
            next_offset=end if more else None,
            total_lines=None if more or not selected else end,
        )
