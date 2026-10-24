"""基于项目环境检测与多语言支持的代码自动格式化运行器模块。

本模块提供代码格式化服务 `CodeFormatter`：
1. 内置 25+ 种常见开发语言格式化工具预设（如 Prettier, Biome, Clang-Format, Ruff, Rustfmt, Gofmt 等）；
2. 智能项目上下文感知：通过向上查找 `package.json`、`pyproject.toml`、`node_modules/.bin` 等文件探测项目本地安装的专用格式化版本；
3. 子进程防悬挂与取消中断：执行格式化命令时通过 `FileMutation.check_cancelled()` 轮询中断信号，超时或取消时强杀进程；
4. 支持多层级配置合并（布尔总开关或针对特定工具的扩展名与环境变量定制）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from threading import Event
from typing import Any

from langchain_core.tools import ToolException
from pydantic import ConfigDict, TypeAdapter
from typing_extensions import TypeForm

from vertical_harness.config.formatter_entry import FormatterEntry
from vertical_harness.execution.process_control import ProcessControl
from vertical_harness.filesystem.file_mutation import FileMutation

# 模块级日志记录器，记录格式化执行失败或退出码异常
logger = logging.getLogger(__name__)

# 格式化配置严格类型适配器：支持布尔开关或以工具名为键的配置字典
_CONFIG: TypeAdapter[bool | dict[str, FormatterEntry]] = TypeAdapter(
    TypeForm(bool | dict[str, FormatterEntry]), config=ConfigDict(strict=True)
)

# JavaScript 与 TypeScript 语言族相关文件后缀元组
_JS: tuple[str, ...] = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")

# Web 前端、标记语言与样式表相关文件后缀元组
_WEB: tuple[str, ...] = (
    *_JS,
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".vue",
    ".svelte",
    ".json",
    ".jsonc",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".md",
    ".mdx",
    ".graphql",
    ".gql",
)

# Ruby 生态相关文件后缀元组
_RUBY: tuple[str, ...] = (".rb", ".rake", ".gemspec", ".ru")

# 内置常用语言格式化工具列表：(工具名, 支持后缀元组, 命令参数模板元组)
_BUILTINS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("gofmt", (".go",), ("-w", "$FILE")),
    ("mix", (".ex", ".exs", ".eex", ".heex", ".leex", ".neex", ".sface"), ("format", "$FILE")),
    ("oxfmt", _JS, ("$FILE",)),
    ("prettier", _WEB, ("--write", "$FILE")),
    ("biome", _WEB, ("format", "--write", "$FILE")),
    ("zig", (".zig", ".zon"), ("fmt", "$FILE")),
    (
        "clang-format",
        (
            ".c",
            ".cc",
            ".cpp",
            ".cxx",
            ".c++",
            ".h",
            ".hh",
            ".hpp",
            ".hxx",
            ".h++",
            ".ino",
            ".C",
            ".H",
        ),
        ("-i", "$FILE"),
    ),
    ("ktlint", (".kt", ".kts"), ("-F", "$FILE")),
    ("ruff", (".py", ".pyi"), ("format", "$FILE")),
    ("air", (".R",), ("format", "$FILE")),
    ("uv", (".py", ".pyi"), ("format", "--", "$FILE")),
    ("rubocop", _RUBY, ("--autocorrect", "$FILE")),
    ("standardrb", _RUBY, ("--fix", "$FILE")),
    ("htmlbeautifier", (".erb", ".html.erb"), ("$FILE",)),
    ("dart", (".dart",), ("format", "$FILE")),
    ("ocamlformat", (".ml", ".mli"), ("-i", "$FILE")),
    ("terraform", (".tf", ".tfvars"), ("fmt", "$FILE")),
    ("latexindent", (".tex",), ("-w", "-s", "$FILE")),
    ("gleam", (".gleam",), ("format", "$FILE")),
    ("shfmt", (".sh", ".bash"), ("-w", "$FILE")),
    ("nixfmt", (".nix",), ("$FILE",)),
    ("rustfmt", (".rs",), ("$FILE",)),
    ("pint", (".php",), ("$FILE",)),
    ("ormolu", (".hs",), ("-i", "$FILE")),
    ("cljfmt", (".clj", ".cljs", ".cljc", ".edn"), ("fix", "--quiet", "$FILE")),
    ("dfmt", (".d",), ("-i", "$FILE")),
)


class CodeFormatter:
    """多语言代码格式化执行器，支持内置规则匹配、工作区级配置覆写与子进程安全格式化。

    Attributes:
        workspace (Path): 所在工作区绝对物理根目录，格式化目标文件必须处于该边界之内。
        directory (Path): 执行格式化命令的物理工作子目录（默认为 workspace）。
        bin_directory (Path | None): 额外的可执行文件查找目录。
        npm_which (Callable[[str], str | None]): 解析局部 npm 二进制文件的查找回调函数。
    """

    def __init__(
        self,
        workspace: Path,
        configured: bool | dict[str, Any] | None = None,
        *,
        directory: Path | None = None,
        bin_directory: Path | None = None,
        npm_which: Callable[[str], str | None] | None = None,
    ) -> None:
        """初始化代码格式化器实例。

        Args:
            workspace (Path): 工作区物理根目录。
            configured (bool | dict[str, Any] | None): 格式化配置（True 启用全部默认、False 全部禁用、或字典定制）。
            directory (Path | None): 格式化命令执行的工作子目录；若未提供则使用 workspace。
            bin_directory (Path | None): 优先搜寻可执行文件的本地二进制目录。
            npm_which (Callable[[str], str | None] | None): 自定义 npm 包二进制解析回调；为 None 时使用默认实现。

        Raises:
            ToolException: 若工作子目录超出工作区边界或不是有效目录时抛出。
        """
        self.workspace = workspace.resolve(strict=True)
        self.directory = (directory or workspace).resolve(strict=True)
        if not self.directory.is_relative_to(self.workspace) or not self.directory.is_dir():
            raise ToolException("Formatter working directory must remain inside the workspace")
        self.bin_directory = bin_directory
        self.npm_which = npm_which or self._npm_which
        self.reload(configured)

    @staticmethod
    def resolve(
        layers: Sequence[bool | dict[str, Any] | None],
    ) -> bool | dict[str, Any] | None:
        """按层叠优先级从前往后合并多层格式化器配置。

        逐层校验各配置层级，布尔值将直接覆写之前的合并状态，字典则对各格式化器规则执行深度合并。

        Args:
            layers (Sequence[bool | dict[str, Any] | None]): 多层配置项序列（从基础配置到高优先级配置）。

        Returns:
            bool | dict[str, Any] | None: 合并后的最终有效配置对象。
        """
        result: bool | dict[str, Any] | None = None
        for layer in layers:
            if layer is None:
                continue
            accepted = _CONFIG.validate_python(layer)
            if isinstance(accepted, bool):
                result = accepted
                continue
            if not isinstance(result, dict):
                result = {}
            for name, entry in accepted.items():
                previous = result.get(name, {})
                supplied = entry.model_dump(exclude_unset=True)
                result[name] = {
                    **previous,
                    **supplied,
                    "environment": {
                        **previous.get("environment", {}),
                        **supplied.get("environment", {}),
                    },
                }
        return result

    def reload(self, configured: bool | dict[str, Any] | None = None) -> None:
        """根据最新配置重载格式化工具集合及对应扩展名规则。

        若配置未禁用，则载入所有内置语言格式化工具（`_BUILTINS`），并根据自定义字典覆写命令或扩展名。

        Args:
            configured (bool | dict[str, Any] | None): 待载入的格式化开关或定制规则字典。
        """
        accepted = None if configured is None else _CONFIG.validate_python(configured)
        entries: dict[str, dict[str, Any]] = {}
        if accepted is not None and accepted is not False:
            for name, extensions, arguments in _BUILTINS:
                entries[name] = {
                    "extensions": extensions,
                    "arguments": arguments,
                    "environment": (
                        {"BUN_BE_BUN": "1"} if name in {"oxfmt", "prettier", "biome"} else {}
                    ),
                }
            if isinstance(accepted, dict):
                for name, entry in accepted.items():
                    if entry.disabled:
                        entries.pop(name, None)
                        continue
                    builtin = entries.get(name)
                    entries[name] = {
                        "extensions": (
                            entry.extensions
                            if entry.extensions is not None
                            else builtin["extensions"]
                            if builtin
                            else []
                        ),
                        "environment": {
                            **(builtin["environment"] if builtin else {}),
                            **(entry.environment or {}),
                        },
                        "command": entry.command,
                        "arguments": builtin["arguments"] if builtin else None,
                    }
        self._entries = entries
        self._commands: dict[str, list[str]] = {}

    def file(self, filepath: str | Path) -> bool:
        """以同步方式为指定文件匹配适用的格式化器并执行就地格式化。

        根据文件后缀匹配注册的格式化工具。首次调用时探测真实命令路径并缓存，
        成功执行且返回码为 0 时返回 True。

        Args:
            filepath (str | Path): 待格式化的物理文件绝对或相对路径。

        Returns:
            bool: 若匹配到格式化工具且成功格式化则返回 True；若无对应工具或执行失败返回 False。

        Raises:
            ToolException: 若目标文件超出工作区范围时抛出。
        """
        path = (self.directory / filepath).resolve()
        if not path.is_relative_to(self.workspace):
            raise ToolException("Formatter target must remain inside the workspace")
        FileMutation.check_cancelled()
        entries, commands = self._entries, self._commands
        dot = path.name.rfind(".")
        extension = path.name[dot:] if dot > 0 else ""
        for name, entry in entries.items():
            if extension not in entry["extensions"]:
                continue
            command = commands.get(name)
            if command is None:
                command = entry.get("command")
                if command is None and entry["arguments"] is not None:
                    command = self._detect(name, entry["arguments"])
                if command is None:
                    continue
                commands[name] = command
            argv = [argument.replace("$FILE", str(path), 1) for argument in command]
            result = self._run(argv, entry["environment"])
            if result is not None and result.returncode == 0:
                return True
        return False

    async def afile(self, filepath: str | Path) -> bool:
        """以异步非阻塞方式调用格式化器，在线程池中运行并在取消时安全等待进程退出。

        Args:
            filepath (str | Path): 待格式化的文件路径。

        Returns:
            bool: 格式化是否成功执行。

        Raises:
            asyncio.CancelledError: 若外层协程在格式化执行期间被取消，确保子进程被强杀后再传播异常。
        """
        cancelled = FileMutation.cancelled.get() or Event()
        token = FileMutation.cancelled.set(cancelled)
        pending = asyncio.create_task(asyncio.to_thread(self.file, filepath))
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            cancelled.set()
            while not pending.done():
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError:
                    continue
            pending.result()
            raise
        finally:
            FileMutation.cancelled.reset(token)

    def _run(
        self,
        command: list[str],
        environment: dict[str, str] | None = None,
        *,
        capture: bool = False,
    ) -> subprocess.CompletedProcess[bytes] | None:
        """拉起格式化命令行进程并捕获标准流输出。

        Args:
            command (list[str]): 命令行参数序列。
            environment (dict[str, str] | None): 额外注入的环境变量。
            capture (bool): 是否捕获 stdout 输出流（用于版本或帮助信息检测）。

        Returns:
            subprocess.CompletedProcess[bytes] | None: 执行结果对象；若启动异常则返回 None。
        """
        env = ProcessControl.environment()
        env.update(environment or {})
        FileMutation.check_cancelled()
        try:
            process = subprocess.Popen(
                command,
                cwd=self.directory,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=os.name != "nt",
            )
        except OSError as error:
            logger.error("Failed to format file with %s: %s", command, error)
            return None

        with process:
            try:
                while True:
                    FileMutation.check_cancelled()
                    try:
                        stdout, stderr = process.communicate(timeout=0.05)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if process.poll() is None:
                    ProcessControl.terminate(process)

        FileMutation.check_cancelled()
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        if result.returncode:
            logger.error(
                "Formatter exited unsuccessfully: %s (exit %s)", command, result.returncode
            )
        return result

    def _find_up(self, target: str) -> list[Path]:
        """向上逐级查找指定文件名直至根工作区。

        Args:
            target (str): 待查找的配置文件名（如 `"package.json"` 或 `".clang-format"`）。

        Returns:
            list[Path]: 沿目录树自底向上命中的所有目标文件绝对路径列表。
        """
        found = []
        current = self.directory
        while True:
            candidate = current / target
            if candidate.exists():
                found.append(candidate)
            if current == self.workspace:
                return found
            current = current.parent

    def _which(self, name: str) -> str | None:
        """在系统环境变量 PATH 及自定义 bin 路径下搜寻可执行程序。

        Args:
            name (str): 可执行程序文件名。

        Returns:
            str | None: 命中的完整可执行绝对路径；未找到时返回 None。
        """
        search = os.environ.get("PATH", "")
        if self.bin_directory is not None:
            search = (
                search + os.pathsep + str(self.bin_directory) if search else str(self.bin_directory)
            )
        return shutil.which(name, path=search)

    def _npm_which(self, package: str) -> str | None:
        """在局部 node_modules/.bin 中向上递归探查可执行包文件。

        Args:
            package (str): npm 依赖包名或路径。

        Returns:
            str | None: 命中的可执行程序绝对路径。
        """
        binary = package.split("/")[-1]
        current = self.directory
        while True:
            match = shutil.which(binary, path=str(current / "node_modules" / ".bin"))
            if match:
                return match
            if current == self.workspace:
                return self._which(binary)
            current = current.parent

    @staticmethod
    def _has_key(value: Any, field: str, key: str) -> bool:
        """辅助判定字典对象中是否存在指定的嵌套依赖键名。

        Args:
            value (Any): 待检查的 JSON 反序列化字典。
            field (str): 第一层字段名（如 `"devDependencies"`）。
            key (str): 目标依赖项键名（如 `"prettier"`）。

        Returns:
            bool: 若存在该嵌套依赖项则返回 True。
        """
        return (
            isinstance(value, dict) and isinstance(value.get(field), dict) and key in value[field]
        )

    @staticmethod
    def _read_text(path: Path) -> str:
        """安全读取小文本文件内容，发生错误时返回空字符串。

        Args:
            path (Path): 待读取的文件路径。

        Returns:
            str: 读取到的文件文本；若发生异常则为空字符串。
        """
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def _detect(self, name: str, arguments: tuple[str, ...]) -> list[str] | None:
        """根据项目上下文和可执行文件存在性探查并生成完整执行命令行。

        Args:
            name (str): 格式化工具名称。
            arguments (tuple[str, ...]): 命令参数模板。

        Returns:
            list[str] | None: 构造出的完整命令参数序列；若环境不满足则为 None。
        """
        if name in {
            "prettier",
            "oxfmt",
            "biome",
            "clang-format",
            "ruff",
            "ocamlformat",
            "pint",
        }:
            try:
                return self._detect_project(name, arguments)
            except Exception:
                return None
        binary = self._which(name)
        if not binary:
            return None
        if name in {"air", "uv"}:
            output = self._run(
                [binary, "--help"] if name == "air" else [binary, "format", "--help"],
                capture=True,
            )
            if output is None or output.returncode != 0:
                return None
            if name == "air":
                first = output.stdout.decode("utf-8", errors="replace").split("\n")[0]
                if "R language" not in first or "formatter" not in first:
                    return None
        return [binary, *arguments]

    def _detect_project(self, name: str, arguments: tuple[str, ...]) -> list[str] | None:
        """依据 package.json、composer.json 或特定配置文件检测项目级格式化工具。

        Args:
            name (str): 项目级工具名。
            arguments (tuple[str, ...]): 预设参数模板。

        Returns:
            list[str] | None: 命中的项目级命令行列表；若项目未显式依赖该工具则返回 None。
        """
        if name in {"prettier", "oxfmt", "pint"}:
            for path in self._find_up("composer.json" if name == "pint" else "package.json"):
                data = json.loads(path.read_text(encoding="utf-8"))
                if name == "pint":
                    if self._has_key(data, "require", "laravel/pint") or self._has_key(
                        data, "require-dev", "laravel/pint"
                    ):
                        return ["./vendor/bin/pint", *arguments]
                elif self._has_key(data, "dependencies", name) or self._has_key(
                    data, "devDependencies", name
                ):
                    binary = self.npm_which(name)
                    if binary:
                        return [binary, *arguments]
            return None

        if name == "biome":
            if not self._find_up("biome.json") and not self._find_up("biome.jsonc"):
                return None
            binary = self.npm_which("@biomejs/biome")
        else:
            binary = self._which(name)
            if name in {"clang-format", "ocamlformat"}:
                if not self._find_up(".clang-format" if name == "clang-format" else ".ocamlformat"):
                    return None
            elif name == "ruff":
                if not binary:
                    return None
                for config in ("pyproject.toml", "ruff.toml", ".ruff.toml"):
                    found = self._find_up(config)
                    if found and (
                        config != "pyproject.toml" or "[tool.ruff]" in self._read_text(found[0])
                    ):
                        return [binary, *arguments]
                for dependency in ("requirements.txt", "pyproject.toml", "Pipfile"):
                    found = self._find_up(dependency)
                    if found and "ruff" in self._read_text(found[0]):
                        return [binary, *arguments]
                return None

        return [binary, *arguments] if binary else None
