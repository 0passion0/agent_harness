"""基于原生 Ripgrep (rg) 进程执行的高性能工作区文件路径检索与内容正则搜索模块。

本模块提供 `RipgrepSearch` 搜索驱动，负责：
1. 原生 `rg` 二进制可执行文件探查与跨平台适配调用；
2. 高性能文件 Glob 通配符路径匹配与隐藏文件过滤控制；
3. 基于 ripgrep `--json` 协议流式解析全文 Grep 结果，结构化捕获物理行号、字节偏移量与高亮区间；
4. 30 秒硬超时管控与子进程强杀防悬挂保障。
"""

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

# ripgrep 子进程搜索的单次最大超时时限（秒）
SEARCH_TIMEOUT: int = 30


class RipgrepSearch:
    """工作区 Ripgrep 执行封装器，支持文件 Glob 通配查找与多条件正则文本 Grep 搜索。

    Attributes:
        root (Path): 允许搜索的物理工作区绝对根路径，任何越出该范围的路径均会被拒绝。
        resolve_path (Callable[[str], Path]): 将虚拟工作区相对路径转换为物理路径的映射函数。
    """

    def __init__(self, root: Path, resolve_path: Callable[[str], Path]) -> None:
        """初始化 RipgrepSearch 实例。

        Args:
            root (Path): 工作区物理根目录路径。
            resolve_path (Callable[[str], Path]): 虚拟路径到物理绝对路径的解析回调函数。
        """
        self.root = root.resolve()
        self.resolve_path = resolve_path

    def _target(self, path: str | None) -> Path:
        """解析虚拟输入路径并强制约束在物理工作区内部。

        Args:
            path (str | None): 待解析的目标相对或绝对路径，None 时默认指向工作区根目录。

        Returns:
            Path: 解析后的目标物理路径。

        Raises:
            ValueError: 当路径尝试通过 `..` 上卷逃逸出工作区根路径时抛出。
        """
        target = self.resolve_path(path or "/").resolve()
        target.relative_to(self.root)
        return target

    def _virtual(self, cwd: Path, name: str) -> str:
        """将物理工作目录下发现的文件条目转换为带前导斜杠的标准虚拟路径。

        Args:
            cwd (Path): 搜索执行时所在的物理工作目录。
            name (str): ripgrep 输出的相对文件名。

        Returns:
            str: 带前导斜杠的规范化虚拟工作区相对路径字符串（例如 `"/src/main.py"`）。
        """
        target = cwd / name
        target.resolve().relative_to(self.root)
        return "/" + target.relative_to(self.root).as_posix()

    @staticmethod
    def _sync(coroutine: Any) -> Any:
        """以安全桥接方式执行异步协程，适配当前无运行循环或运行中事件循环环境。

        Args:
            coroutine (Any): 待同步等待的异步协程对象。

        Returns:
            Any: 协程执行后的返回结果。
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, coroutine).result()

    async def _run(
        self, cwd: Path, args: list[str], limit: int, *, grep: bool
    ) -> tuple[list[Any], bool]:
        """拉起 ripgrep 原生子进程，并配合 30 秒硬超时捕获标准流与错误流。

        优先寻找环境内安装的 `rg`，若未配置则从 `ripgrep-bin` 轮子包中动态定位。
        超时或异常时强制销毁子进程。

        Args:
            cwd (Path): 命令执行所在的物理目录。
            args (list[str]): 传递给 `rg` 命令行的参数列表。
            limit (int): 最大匹配行数上限。
            grep (bool): 是否为全文 Grep 模式（True 表示按 JSON 流解析）。

        Returns:
            tuple[list[Any], bool]: 二元组 `(rows, truncated)`，包含解析后的行对象列表与是否触顶截断布尔值。

        Raises:
            ValueError: 若系统未安装 ripgrep 且无内嵌二进制时抛出。
            TimeoutError: 当命令执行超出 30 秒时抛出。
            OSError: 当子进程异常崩溃退出时抛出。
        """
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
        """异步读取并缓冲子进程的错误输出流，上限 8192 字节。

        Args:
            process (asyncio.subprocess.Process): 异步子进程对象。

        Returns:
            bytes: 读取到的标准错误原始字节流。
        """
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
        """流式分片读取标准输出并反序列化行条目，检测结果上限截断。

        Args:
            process (asyncio.subprocess.Process): 运行中的子进程对象。
            error_task (asyncio.Task[bytes]): 正在读取标准错误的后台任务。
            limit (int): 最大条目限制配额。
            grep (bool): 是否需要将行作为 JSON 反序列化解析。

        Returns:
            tuple[list[Any], bool]: 收集到的匹配行列表及是否截断标记。

        Raises:
            ValueError: 当正则表达式解析失败时抛出。
            OSError: 当进程返回非预期错误码时抛出。
        """
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
        """以同步方式在工作区内执行文件名通配模式匹配。

        内部通过安全桥接调用 `aglob`。

        Args:
            pattern (str): 文件名通配表达式（如 `"**/*.py"` 或 `"*.json"`）。
            path (str | None): 可选的起始搜索相对子目录。
            limit (int): 最大返回匹配文件数量，默认 100。
            hidden (bool): 是否包含隐藏文件（以点开头的文件），默认 False。

        Returns:
            GlobResult: 包含匹配路径列表 `matches`、截断标记 `truncated` 与错误信息 `error` 的结果对象。
        """
        return self._sync(self.aglob(pattern, path, limit, hidden=hidden))

    async def aglob(
        self, pattern: str, path: str | None = None, limit: int = 100, *, hidden: bool = False
    ) -> GlobResult:
        """以异步非阻塞方式在工作区内执行文件名通配模式匹配。

        调用 `rg --files --glob=<pattern>` 快速定位匹配文件，自动排除 `.git` 目录。

        Args:
            pattern (str): 通配符匹配表达式。
            path (str | None): 可选的搜索子目录。
            limit (int): 返回结果数量上限。
            hidden (bool): 是否搜寻隐藏文件。

        Returns:
            GlobResult: 匹配的文件列表对象。
        """
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
        """以同步方式执行文本 Grep 搜索并提取丰富行和子匹配元数据。

        内部通过安全桥接调用 `agrep`。

        Args:
            pattern (str): 待检索的目标关键词或正则表达式字符串。
            path (str | None): 可选的搜索文件或目录路径。
            glob (str | None): 仅在满足特定通配符的文件中检索（如 `"*.py"`）。
            max_count (int): 最大允许返回的匹配行数，默认 100。
            literal (bool): 是否按字面常量文本搜索（False 时作为正则表达式处理），默认 True。
            case_sensitive (bool): 是否区分字母大小写，默认 True。

        Returns:
            StructuredGrepResult: 结构化匹配结果，包含基础匹配行列表及细粒度行号与偏移元数据。
        """
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
        """以异步非阻塞方式调用 ripgrep 执行全文检索并封装结构化结果。

        利用 ripgrep `--json` 特性，提取每一处的精确物理行号、字节偏移量与高亮区间。

        Args:
            pattern (str): 关键词或正则匹配文本。
            path (str | None): 目标搜索文件或目录。
            glob (str | None): 文件过滤通配模式。
            max_count (int): 最大返回匹配条目数。
            literal (bool): 是否按字面纯文本精确匹配。
            case_sensitive (bool): 是否严格区分大小写。

        Returns:
            StructuredGrepResult: 封装完成的结构化 Grep 搜索结果对象。
        """
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
