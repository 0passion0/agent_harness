"""Ripgrep search semantics adapted from OpenCode's search tools."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from importlib import metadata
from pathlib import Path
from typing import Any

from deepagents.backends.filesystem import _resolve_ripgrep_path
from deepagents.backends.protocol import GlobResult

from .opencode_grep_result import OpenCodeGrepResult

SEARCH_TIMEOUT = 30


class RipgrepSearch:
    def __init__(self, root: Path, resolve_path: Callable[[str], Path]) -> None:
        self.root = root.resolve()
        self.resolve_path = resolve_path

    def _target(self, path: str | None) -> Path:
        target = self.resolve_path(path or "/").resolve()
        target.relative_to(self.root)
        return target

    def _virtual(self, cwd: Path, name: str) -> str:
        target = cwd / name
        target.resolve().relative_to(self.root)
        return "/" + target.relative_to(self.root).as_posix()

    @staticmethod
    def _sync(coroutine: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, coroutine).result()

    async def _run(
        self, cwd: Path, args: list[str], limit: int, *, grep: bool
    ) -> tuple[list[Any], bool]:
        binary = _resolve_ripgrep_path()
        if binary is None:
            try:
                distribution = metadata.distribution("ripgrep-bin")
            except metadata.PackageNotFoundError:
                raise ValueError("ripgrep ('rg') is required for filesystem search") from None
            binary = str(
                distribution.locate_file(
                    next(file for file in distribution.files if Path(file).name in {"rg", "rg.exe"})
                )
            )
        process = await asyncio.create_subprocess_exec(
            binary,
            "--no-config",
            *args,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None and process.stderr is not None

        error_task = asyncio.create_task(self._stderr(process))
        try:
            async with asyncio.timeout(SEARCH_TIMEOUT):
                return await self._collect(process, error_task, limit, grep=grep)
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()
            await error_task

    async def _stderr(self, process: asyncio.subprocess.Process) -> bytes:
        captured = bytearray()
        while chunk := await process.stderr.read(8192):
            captured.extend(chunk[: max(0, 8192 - len(captured))])
        return bytes(captured)

    async def _collect(
        self,
        process: asyncio.subprocess.Process,
        error_task: asyncio.Task[bytes],
        limit: int,
        *,
        grep: bool,
    ) -> tuple[list[Any], bool]:
        rows: list[Any] = []
        fragments: list[bytes] = []
        while chunk := await process.stdout.read(65536):
            parts = chunk.split(b"\n")
            for part in parts[:-1]:
                line = b"".join([*fragments, part]).decode("utf-8", errors="replace")
                fragments.clear()
                if not line:
                    continue
                row = json.loads(line) if grep else line
                if grep and row["type"] != "match":
                    continue
                rows.append(row)
                if len(rows) > limit:
                    return rows[:limit], True
            fragments.append(parts[-1])
        if fragments and (line := b"".join(fragments).decode("utf-8", errors="replace")):
            row = json.loads(line) if grep else line
            if not grep or row["type"] == "match":
                rows.append(row)
        code = await process.wait()
        error = (await error_task).decode("utf-8", errors="replace").strip()
        if grep and code == 2 and ("regex parse error" in error or "error parsing regex" in error):
            raise ValueError(f"Invalid regex pattern: {error}")
        if code not in (0, 1, 2):
            raise OSError(error or f"ripgrep failed with code {code}")
        return ([] if code == 1 else rows[:limit]), len(rows) > limit

    def glob(
        self, pattern: str, path: str | None = None, limit: int = 100, *, hidden: bool = False
    ) -> GlobResult:
        return self._sync(self.aglob(pattern, path, limit, hidden=hidden))

    async def aglob(
        self, pattern: str, path: str | None = None, limit: int = 100, *, hidden: bool = False
    ) -> GlobResult:
        if limit <= 0:
            return GlobResult(error="limit must be positive")
        path = None if path in ("undefined", "null") else path
        try:
            cwd = self._target(path)
            if not cwd.exists():
                return GlobResult(error=f"Search path does not exist: {path or '.'}")
            if not cwd.is_dir():
                return GlobResult(error=f"Search path is not a directory: {path or '.'}")
            args = [
                "--files",
                *(["--hidden"] if hidden else []),
                f"--glob={pattern}",
                *([] if hidden else ["--glob=!**/.*"]),
                "--glob=!**/.git/**",
                ".",
            ]
            rows, truncated = await self._run(cwd, args, limit, grep=False)
            return GlobResult(
                matches=[{"path": self._virtual(cwd, row), "is_dir": False} for row in rows],
                truncated=truncated,
            )
        except TimeoutError:
            return GlobResult(
                error=f"Search timed out after {SEARCH_TIMEOUT} seconds. Consider using a more specific path or pattern."
            )
        except (OSError, ValueError) as error:
            return GlobResult(error=str(error))

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int = 100,
        literal: bool = True,
        case_sensitive: bool = True,
    ) -> OpenCodeGrepResult:
        return self._sync(
            self.agrep(
                pattern,
                path,
                glob,
                max_count=max_count,
                literal=literal,
                case_sensitive=case_sensitive,
            )
        )

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int = 100,
        literal: bool = True,
        case_sensitive: bool = True,
    ) -> OpenCodeGrepResult:
        if not pattern:
            return OpenCodeGrepResult(error="Pattern must not be empty")
        if max_count <= 0:
            return OpenCodeGrepResult(error="max_count must be positive")
        try:
            target = self._target(path)
            if not target.exists():
                return OpenCodeGrepResult(error=f"Search path does not exist: {path or '.'}")
            cwd = target if target.is_dir() else target.parent
            args = [
                "--json",
                "--hidden",
                "--no-messages",
                *(["--fixed-strings"] if literal else []),
                *([] if case_sensitive else ["--ignore-case"]),
                *([f"--glob={glob}"] if glob else []),
                "--glob=!**/.git/**",
                "--",
                pattern,
                "." if target.is_dir() else target.name,
            ]
            rows, truncated = await self._run(cwd, args, max_count, grep=True)
            matches = []
            source_matches = []
            for row in rows:
                data = row["data"]
                text = data["lines"]["text"]
                # OpenCode measures its preview in JavaScript UTF-16 code units.
                units = text.encode("utf-16-le")
                preview = (
                    units[:4000].decode("utf-16-le", errors="surrogatepass") + "..."
                    if len(units) > 4000
                    else text
                )
                virtual = self._virtual(cwd, data["path"]["text"])
                source_matches.append(
                    {
                        "entry": {"path": virtual, "type": "file"},
                        "line": data["line_number"],
                        "offset": data["absolute_offset"],
                        "text": preview,
                        "submatches": [
                            {
                                "text": match["match"]["text"],
                                "start": match["start"],
                                "end": match["end"],
                            }
                            for match in data["submatches"][:100]
                        ],
                    }
                )
                matches.append(
                    {
                        "path": virtual,
                        "line": data["line_number"],
                        "text": preview,
                    }
                )
            return OpenCodeGrepResult(
                matches=matches, truncated=truncated, source_matches=source_matches
            )
        except TimeoutError:
            return OpenCodeGrepResult(
                error=f"Search timed out after {SEARCH_TIMEOUT} seconds. Consider using a more specific path or pattern."
            )
        except (OSError, ValueError) as error:
            return OpenCodeGrepResult(error=str(error))
