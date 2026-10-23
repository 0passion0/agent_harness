"""基于原生 Ripgrep (rg) 进程执行的高性能工作区文件路径检索与内容正则搜索模块。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from importlib import metadata
from pathlib import Path
from typing import Any

from deepagents.backends.filesystem import _resolve_ripgrep_path
from deepagents.backends.protocol import GlobResult, GrepMatch

from vertical_harness.filesystem.structured_grep_result import StructuredGrepResult

SEARCH_TIMEOUT = 30


class RipgrepSearch:
    """工作区 Ripgrep 执行封装器，支持文件 Glob 通配查找与多条件正则文本 Grep 搜索。"""

    def __init__(self, root: Path, resolve_path: Callable[[str], Path]) -> None:
        """初始化 RipgrepSearch 实例。"""
        self.root = root.resolve()
        self.resolve_path = resolve_path

    def _target(self, path: str | None) -> Path:
        """解析虚拟输入路径并强制约束在物理工作区内部。"""
        target = self.resolve_path(path or "/").resolve()
        target.relative_to(self.root)
        return target

    def _virtual(self, cwd: Path, name: str) -> str:
        """将物理工作目录下发现的文件条目转换为带前导斜杠的标准虚拟路径。"""
        target = cwd / name
        target.resolve().relative_to(self.root)
        return "/" + target.relative_to(self.root).as_posix()

    @staticmethod
    def _sync(coroutine: Any) -> Any:
        """以安全桥接方式执行异步协程，适配当前无运行循环或运行中事件循环环境。"""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, coroutine).result()

    async def _run(
        self, cwd: Path, args: list[str], limit: int, *, grep: bool
    ) -> tuple[list[Any], bool]:
        """拉起 ripgrep 原生子进程，并配合 30 秒硬超时捕获标准流与错误流。"""
        binary = _resolve_ripgrep_path()
        if binary is None:
            try:
                distribution = metadata.distribution("ripgrep-bin")
            except metadata.PackageNotFoundError:
                raise ValueError("ripgrep ('rg') is required for filesystem search") from None
            binary = str(
                distribution.locate_file(
                    next(
                        file
                        for file in (distribution.files or [])
                        if Path(file).name in {"rg", "rg.exe"}
                    )
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
        """异步读取并缓冲子进程的错误输出流，上限 8192 字节。"""
        assert process.stderr is not None
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
        """流式分片读取标准输出并反序列化行条目，检测结果上限截断。"""
        assert process.stdout is not None
        rows: list[Any] = []
        fragments: list[bytes] = []
        while chunk := await process.stdout.read(65536):
            parts = chunk.split(b"\n")
            for part in parts[:-1]:
                line = b"".join([*fragments, part]).decode("utf-8", errors="replace")
                fragments.clear()
                if not line:
                    continue
                row: Any = json.loads(line) if grep else line
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
        """以同步方式在工作区内执行文件名通配模式匹配。"""
        return self._sync(self.aglob(pattern, path, limit, hidden=hidden))

    async def aglob(
        self, pattern: str, path: str | None = None, limit: int = 100, *, hidden: bool = False
    ) -> GlobResult:
        """以异步非阻塞方式在工作区内执行文件名通配模式匹配。"""
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
        except Exception as error:
            return GlobResult(error=f"{type(error).__name__}: {error}")

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int = 100,
        literal: bool = True,
        case_sensitive: bool = True,
    ) -> StructuredGrepResult:
        """以同步方式执行文本 Grep 搜索并提取丰富行和子匹配元数据。"""
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
    ) -> StructuredGrepResult:
        """以异步非阻塞方式调用 ripgrep 执行全文检索并封装结构化结果。"""
        if not pattern:
            return StructuredGrepResult(error="Pattern must not be empty")
        if max_count <= 0:
            return StructuredGrepResult(error="max_count must be positive")

        try:
            target = self._target(path)
            if not target.exists():
                return StructuredGrepResult(error=f"Search path does not exist: {path or '.'}")
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
            matches: list[GrepMatch] = []
            source_matches = []
            for row in rows:
                data = row["data"]
                text = data["lines"]["text"]
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
            return StructuredGrepResult(
                matches=matches, truncated=truncated, source_matches=source_matches
            )
        except TimeoutError:
            return StructuredGrepResult(
                error=f"Search timed out after {SEARCH_TIMEOUT} seconds. Consider using a more specific path or pattern."
            )
        except Exception as error:
            return StructuredGrepResult(error=f"{type(error).__name__}: {error}")
