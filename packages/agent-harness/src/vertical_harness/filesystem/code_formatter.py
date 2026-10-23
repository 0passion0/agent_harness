"""基于项目环境检测与多语言支持的代码自动格式化运行器模块。"""

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

from vertical_harness.filesystem.file_mutation import FileMutation
from vertical_harness.config.formatter_entry import FormatterEntry
from vertical_harness.execution.shell_execution import ShellExecution

logger = logging.getLogger(__name__)
_CONFIG = TypeAdapter(bool | dict[str, FormatterEntry], config=ConfigDict(strict=True))
_JS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
_WEB = (
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
_RUBY = (".rb", ".rake", ".gemspec", ".ru")
_BUILTINS = (
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

    :author: tongliga
    :date: 2026-10-21
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

        :param workspace: 格式化器运行约束的物理根工作区路径。
        :type workspace: pathlib.Path
        :param configured: 格式化全局开关（布尔值）或各工具配置项字典。
        :type configured: bool | dict[str, typing.Any] | None
        :param directory: 当前格式化工作目录（必须为 workspace 子目录），缺省为 workspace。
        :type directory: pathlib.Path | None
        :param bin_directory: 注入 PATH 检索的前置自定义可执行文件目录。
        :type bin_directory: pathlib.Path | None
        :param npm_which: 自定义 node_modules/.bin 解析寻址函数。
        :type npm_which: collections.abc.Callable[[str], str | None] | None
        :raises ToolException: 当工作目录越出工作区边界或不是有效目录时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：校验并解析绝对工作区与执行目录
        self.workspace = workspace.resolve(strict=True)
        self.directory = (directory or workspace).resolve(strict=True)
        if not self.directory.is_relative_to(self.workspace) or not self.directory.is_dir():
            raise ToolException("Formatter working directory must remain inside the workspace")
        self.bin_directory = bin_directory
        self.npm_which = npm_which or self._npm_which
        # 第二步：加载并激活配置项
        self.reload(configured)

    @staticmethod
    def resolve(
        layers: Sequence[bool | dict[str, Any] | None],
    ) -> bool | dict[str, Any] | None:
        """按层叠优先级从前往后合并多层格式化器配置。

        :param layers: 配置层切片，后层覆盖前层配置。
        :type layers: collections.abc.Sequence[bool | dict[str, typing.Any] | None]
        :return: 合并解析后的格式化器最终配置。
        :rtype: bool | dict[str, typing.Any] | None
        :author: tongliga
        :date: 2026-10-21
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

        :param configured: 格式化配置（布尔开关或规则字典）。
        :type configured: bool | dict[str, typing.Any] | None
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：校验并规整配置字典
        accepted = None if configured is None else _CONFIG.validate_python(configured)
        entries: dict[str, dict[str, Any]] = {}

        # 第二步：如果格式化器未被完全禁用（not False），注册内置规则
        if accepted is not None and accepted is not False:
            for name, extensions, arguments in _BUILTINS:
                entries[name] = {
                    "extensions": extensions,
                    "arguments": arguments,
                    "environment": (
                        {"BUN_BE_BUN": "1"} if name in {"oxfmt", "prettier", "biome"} else {}
                    ),
                }
            # 第三步：叠加用户自定义或工作区层配置
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
                            else builtin["extensions"] if builtin else []
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

        :param filepath: 目标文件相对路径或 Path 对象。
        :type filepath: str | pathlib.Path
        :return: 格式化是否成功执行且文件被格式化器成功修改/校验。
        :rtype: bool
        :raises ToolException: 目标路径越出工作区边界时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：路径越界检查与协作取消校验
        path = (self.directory / filepath).resolve()
        if not path.is_relative_to(self.workspace):
            raise ToolException("Formatter target must remain inside the workspace")
        FileMutation.check_cancelled()

        # 第二步：依据文件扩展名匹配规则并执行首个可用工具
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

        :param filepath: 目标文件相对路径或 Path 对象。
        :type filepath: str | pathlib.Path
        :return: 格式化是否成功完成。
        :rtype: bool
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：关联文件变更取消事件 token
        cancelled = FileMutation.cancelled.get() or Event()
        token = FileMutation.cancelled.set(cancelled)
        pending = asyncio.create_task(asyncio.to_thread(self.file, filepath))

        # 第二步：受保护等待，避免在任务取消时过早释放文件写锁
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

        :param command: 完整的命令行参数数组。
        :type command: list[str]
        :param environment: 注入的自定义环境变量字典。
        :type environment: dict[str, str] | None
        :param capture: 是否在内存中捕获标准输出流。
        :type capture: bool
        :return: 已完成进程结果对象，拉起失败时返回 None。
        :rtype: subprocess.CompletedProcess[bytes] | None
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：构建干净的环境变量白名单
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE"}
        }
        env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        env.update(environment or {})
        FileMutation.check_cancelled()

        # 第二步：启动子进程并监控取消状态
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
                    ShellExecution._stop_background({"process": process})

        FileMutation.check_cancelled()
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        if result.returncode:
            logger.error(
                "Formatter exited unsuccessfully: %s (exit %s)", command, result.returncode
            )
        return result

    def _find_up(self, target: str) -> list[Path]:
        """向上逐级查找指定文件名直至根工作区。

        :param target: 目标配置文件名。
        :type target: str
        :return: 查找到的配置文件路径列表。
        :rtype: list[pathlib.Path]
        :author: tongliga
        :date: 2026-10-21
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

        :param name: 可执行文件名。
        :type name: str
        :return: 可执行文件绝对路径或 None。
        :rtype: str | None
        :author: tongliga
        :date: 2026-10-21
        """
        search = os.environ.get("PATH", "")
        if self.bin_directory is not None:
            search = (
                search + os.pathsep + str(self.bin_directory) if search else str(self.bin_directory)
            )
        return shutil.which(name, path=search)

    def _npm_which(self, package: str) -> str | None:
        """在局部 node_modules/.bin 中向上递归探查可执行包文件。

        :param package: npm 包名或命令名。
        :type package: str
        :return: 可执行脚本路径或 None。
        :rtype: str | None
        :author: tongliga
        :date: 2026-10-21
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

        :param value: JSON 对象。
        :type value: typing.Any
        :param field: 第一层字段名。
        :type field: str
        :param key: 第二层键名。
        :type key: str
        :return: 是否存在该键。
        :rtype: bool
        :author: tongliga
        :date: 2026-10-21
        """
        return (
            isinstance(value, dict)
            and isinstance(value.get(field), dict)
            and key in value[field]
        )

    @staticmethod
    def _read_text(path: Path) -> str:
        """安全读取小文本文件内容，发生错误时返回空字符串。

        :param path: 本地文件路径。
        :type path: pathlib.Path
        :return: 读取到的字符串文本。
        :rtype: str
        :author: tongliga
        :date: 2026-10-21
        """
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def _detect(self, name: str, arguments: tuple[str, ...]) -> list[str] | None:
        """根据项目上下文和可执行文件存在性探查并生成完整执行命令行。

        :param name: 格式化器标识名。
        :type name: str
        :param arguments: 默认参数元组。
        :type arguments: tuple[str, ...]
        :return: 拼接后的完整执行参数列表或 None。
        :rtype: list[str] | None
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：针对特定项目类型检查专属配置及 node/vendor 依赖
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

        # 第二步：针对通用工具检查 PATH 是否包含可执行文件
        binary = self._which(name)
        if not binary:
            return None

        # 第三步：针对特殊工具（如 air / uv）执行 --help 探针验证真实身份
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

        :param name: 工具标识名。
        :type name: str
        :param arguments: 默认格式化参数元组。
        :type arguments: tuple[str, ...]
        :return: 生成的命令行数组或 None。
        :rtype: list[str] | None
        :author: tongliga
        :date: 2026-10-21
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
                if not self._find_up(
                    ".clang-format" if name == "clang-format" else ".ocamlformat"
                ):
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
