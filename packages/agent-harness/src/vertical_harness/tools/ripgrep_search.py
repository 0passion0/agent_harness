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
from deepagents.backends.protocol import GlobResult

from vertical_harness.tools.structured_grep_result import StructuredGrepResult

SEARCH_TIMEOUT = 30


class RipgrepSearch:
    """工作区 Ripgrep 执行封装器，支持文件 Glob 通配查找与多条件正则文本 Grep 搜索。

    :author: tongliga
    :date: 2026-10-21
    """

    def __init__(self, root: Path, resolve_path: Callable[[str], Path]) -> None:
        """初始化 RipgrepSearch 实例。

        :param root: 工作区物理根目录 Path 对象。
        :type root: pathlib.Path
        :param resolve_path: 虚拟工作区相对路径到绝对物理路径的解析映射回调函数。
        :type resolve_path: collections.abc.Callable[[str], pathlib.Path]
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：保存绝对解析根路径与路径解析回调
        self.root = root.resolve()
        self.resolve_path = resolve_path

    def _target(self, path: str | None) -> Path:
        """解析虚拟输入路径并强制约束在物理工作区内部。

        :param path: 虚拟路径字符串（如 '/src' 或 None）。
        :type path: str | None
        :return: 解析后的安全绝对物理 Path。
        :rtype: pathlib.Path
        :raises ValueError: 当路径越界时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        target = self.resolve_path(path or "/").resolve()
        target.relative_to(self.root)
        return target

    def _virtual(self, cwd: Path, name: str) -> str:
        """将物理工作目录下发现的文件条目转换为带前导斜杠的标准虚拟路径。

        :param cwd: 搜索运行所在的物理工作目录。
        :type cwd: pathlib.Path
        :param name: 条目相对文件名。
        :type name: str
        :return: 对应的标准虚拟路径。
        :rtype: str
        :author: tongliga
        :date: 2026-10-21
        """
        target = cwd / name
        target.resolve().relative_to(self.root)
        return "/" + target.relative_to(self.root).as_posix()

    @staticmethod
    def _sync(coroutine: Any) -> Any:
        """以安全桥接方式执行异步协程，适配当前无运行循环或运行中事件循环环境。

        :param coroutine: 待同步等待的协程对象。
        :type coroutine: typing.Any
        :return: 协程执行完成返回的结果。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-21
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

        :param cwd: 子进程运行的工作物理目录。
        :type cwd: pathlib.Path
        :param args: 传递给 rg 可执行程序的完整参数列表。
        :type args: list[str]
        :param limit: 最大允许收集的记录行数。
        :type limit: int
        :param grep: 是否为 Grep 匹配模式（True 解析 JSON 匹配，False 解析行流）。
        :type grep: bool
        :return: (结果数据行列表, 是否发生截断) 元组。
        :rtype: tuple[list[typing.Any], bool]
        :raises ValueError: 找不到 ripgrep 二进制文件或正则表达式解析失败时抛出。
        :raises OSError: 子进程退出异常或底层系统调用失败时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：寻址 rg 二进制程序路径
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

        # 第二步：异步创建 rg 执行子进程
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

        # 第三步：设定超时并异步收集输出行
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

        :param process: 运行中的异步进程对象。
        :type process: asyncio.subprocess.Process
        :return: 错误流原始字节。
        :rtype: bytes
        :author: tongliga
        :date: 2026-10-21
        """
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

        :param process: 运行中的异步子进程。
        :type process: asyncio.subprocess.Process
        :param error_task: 异步错误输出捕获 Task。
        :type error_task: asyncio.Task[bytes]
        :param limit: 最大收集记录限制。
        :type limit: int
        :param grep: 是否为 grep 结构化匹配。
        :type grep: bool
        :return: (结果行列表, 是否超过限制截断) 元组。
        :rtype: tuple[list[typing.Any], bool]
        :author: tongliga
        :date: 2026-10-21
        """
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
        """以同步方式在工作区内执行文件名通配模式匹配。

        :param pattern: 文件匹配通配符表达式（如 '*.py'、'src/**'）。
        :type pattern: str
        :param path: 相对搜索起始目录（默认根目录）。
        :type path: str | None
        :param limit: 最大返回匹配路径数量（默认 100）。
        :type limit: int
        :param hidden: 是否检索包含点号前缀的隐藏文件（排除 .git）。
        :type hidden: bool
        :return: GlobResult 匹配结果模型。
        :rtype: deepagents.backends.protocol.GlobResult
        :author: tongliga
        :date: 2026-10-21
        """
        return self._sync(self.aglob(pattern, path, limit, hidden=hidden))

    async def aglob(
        self, pattern: str, path: str | None = None, limit: int = 100, *, hidden: bool = False
    ) -> GlobResult:
        """以异步非阻塞方式在工作区内执行文件名通配模式匹配。

        :param pattern: 文件通配符表达式。
        :type pattern: str
        :param path: 相对搜索起始目录。
        :type path: str | None
        :param limit: 最大返回结果数量。
        :type limit: int
        :param hidden: 是否包含隐藏文件。
        :type hidden: bool
        :return: 包含虚拟路径列表的 GlobResult。
        :rtype: deepagents.backends.protocol.GlobResult
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：入参合法性断言
        if limit <= 0:
            return GlobResult(error="limit must be positive")
        path = None if path in ("undefined", "null") else path

        # 第二步：校验搜索目标目录
        try:
            cwd = self._target(path)
            if not cwd.exists():
                return GlobResult(error=f"Search path does not exist: {path or '.'}")
            if not cwd.is_dir():
                return GlobResult(error=f"Search path is not a directory: {path or '.'}")

            # 第三步：组装 rg --files 命令参数
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

        :param pattern: 待匹配的目标字面量字符串或正则表达式。
        :type pattern: str
        :param path: 检索起始相对路径（目录或具体文件）。
        :type path: str | None
        :param glob: 可选的文件名过滤模式（如 '*.json'）。
        :type glob: str | None
        :param max_count: 最大匹配结果条目数限制（默认 100）。
        :type max_count: int
        :param literal: 是否作为纯文本字面量处理（True 开启 --fixed-strings）。
        :type literal: bool
        :param case_sensitive: 是否严格区分大小写（默认 True）。
        :type case_sensitive: bool
        :return: 包含 matches 与 source_matches 的结构化检索结果对象。
        :rtype: vertical_harness.tools.structured_grep_result.StructuredGrepResult
        :author: tongliga
        :date: 2026-10-21
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

        :param pattern: 检索目标模式字符串。
        :type pattern: str
        :param path: 相对搜索起始路径。
        :type path: str | None
        :param glob: 文件名过滤通配符。
        :type glob: str | None
        :param max_count: 最大匹配上限。
        :type max_count: int
        :param literal: 是否为字面量匹配。
        :type literal: bool
        :param case_sensitive: 是否区分大小写。
        :type case_sensitive: bool
        :return: 结构化匹配结果对象 StructuredGrepResult。
        :rtype: vertical_harness.tools.structured_grep_result.StructuredGrepResult
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：基础参数非空与范围校验
        if not pattern:
            return StructuredGrepResult(error="Pattern must not be empty")
        if max_count <= 0:
            return StructuredGrepResult(error="max_count must be positive")

        try:
            # 第二步：确定搜索目标物理路径并确定工作目录
            target = self._target(path)
            if not target.exists():
                return StructuredGrepResult(error=f"Search path does not exist: {path or '.'}")
            cwd = target if target.is_dir() else target.parent

            # 第三步：组装 rg --json 命令行参数
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

            # 第四步：解析 JSON 匹配流并格式化预览行及子匹配区间
            matches = []
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
