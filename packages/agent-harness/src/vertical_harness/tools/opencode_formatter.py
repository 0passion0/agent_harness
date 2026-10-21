"""Formatter behavior ported from OpenCode 7d3752c (MIT; see THIRD_PARTY_NOTICES)."""

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

from vertical_harness.storage.file_mutation import FileMutation
from vertical_harness.tools.opencode_formatter_entry import OpenCodeFormatterEntry
from vertical_harness.tools.shell_execution import ShellExecution

logger = logging.getLogger(__name__)
_CONFIG = TypeAdapter(bool | dict[str, OpenCodeFormatterEntry], config=ConfigDict(strict=True))
_JS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
_WEB = (*_JS, ".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue", ".svelte",
        ".json", ".jsonc", ".yaml", ".yml", ".toml", ".xml", ".md", ".mdx", ".graphql", ".gql")
_RUBY = (".rb", ".rake", ".gemspec", ".ru")
_BUILTINS = (
    ("gofmt", (".go",), ("-w", "$FILE")),
    ("mix", (".ex", ".exs", ".eex", ".heex", ".leex", ".neex", ".sface"), ("format", "$FILE")),
    ("oxfmt", _JS, ("$FILE",)),
    ("prettier", _WEB, ("--write", "$FILE")),
    ("biome", _WEB, ("format", "--write", "$FILE")),
    ("zig", (".zig", ".zon"), ("fmt", "$FILE")),
    ("clang-format", (".c", ".cc", ".cpp", ".cxx", ".c++", ".h", ".hh", ".hpp", ".hxx", ".h++", ".ino", ".C", ".H"), ("-i", "$FILE")),
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


class OpenCodeFormatter:
    def __init__(
        self,
        workspace: Path,
        configured: bool | dict[str, Any] | None = None,
        *,
        directory: Path | None = None,
        bin_directory: Path | None = None,
        npm_which: Callable[[str], str | None] | None = None,
    ) -> None:
        self.workspace = workspace.resolve(strict=True)
        self.directory = (directory or workspace).resolve(strict=True)
        if not self.directory.is_relative_to(self.workspace) or not self.directory.is_dir():
            raise ToolException("Formatter working directory must remain inside the workspace")
        self.bin_directory = bin_directory
        self.npm_which = npm_which or self._npm_which
        self.reload(configured)

    @staticmethod
    def resolve(layers: Sequence[bool | dict[str, Any] | None]) -> bool | dict[str, Any] | None:
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
                    "environment": {**previous.get("environment", {}), **supplied.get("environment", {})},
                }
        return result

    def reload(self, configured: bool | dict[str, Any] | None = None) -> None:
        accepted = None if configured is None else _CONFIG.validate_python(configured)
        entries: dict[str, dict[str, Any]] = {}
        if accepted is not None and accepted is not False:
            for name, extensions, arguments in _BUILTINS:
                entries[name] = {
                    "extensions": extensions,
                    "arguments": arguments,
                    "environment": {"BUN_BE_BUN": "1"} if name in {"oxfmt", "prettier", "biome"} else {},
                }
            if isinstance(accepted, dict):
                for name, entry in accepted.items():
                    if entry.disabled:
                        entries.pop(name, None)
                        continue
                    builtin = entries.get(name)
                    entries[name] = {
                        "extensions": entry.extensions if entry.extensions is not None else builtin["extensions"] if builtin else [],
                        "environment": {**(builtin["environment"] if builtin else {}), **(entry.environment or {})},
                        "command": entry.command,
                        "arguments": builtin["arguments"] if builtin else None,
                    }
        self._entries = entries
        self._commands: dict[str, list[str]] = {}

    def file(self, filepath: str | Path) -> bool:
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
        cancelled = FileMutation.cancelled.get() or Event()
        token = FileMutation.cancelled.set(cancelled)
        pending = asyncio.create_task(asyncio.to_thread(self.file, filepath))
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            cancelled.set()
            # A cancelled edit must not release its mutation lock while a formatter still writes.
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
        self, command: list[str], environment: dict[str, str] | None = None, *, capture: bool = False
    ) -> subprocess.CompletedProcess[bytes] | None:
        env = {
            key: value for key, value in os.environ.items()
            if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE"}
        }
        env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        env.update(environment or {})
        FileMutation.check_cancelled()
        try:
            process = subprocess.Popen(
                command, cwd=self.directory, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=os.name != "nt",
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
            logger.error("Formatter exited unsuccessfully: %s (exit %s)", command, result.returncode)
        return result

    def _find_up(self, target: str) -> list[Path]:
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
        search = os.environ.get("PATH", "")
        if self.bin_directory is not None:
            search = search + os.pathsep + str(self.bin_directory) if search else str(self.bin_directory)
        return shutil.which(name, path=search)

    def _npm_which(self, package: str) -> str | None:
        # shortcut: no managed npm install/cache, inject npm_which when package installation is required.
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
        return isinstance(value, dict) and isinstance(value.get(field), dict) and key in value[field]

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return ""

    def _detect(self, name: str, arguments: tuple[str, ...]) -> list[str] | None:
        if name in {"prettier", "oxfmt", "biome", "clang-format", "ruff", "ocamlformat", "pint"}:
            try:
                return self._detect_project(name, arguments)
            except (OSError, ValueError):
                return None
        binary = self._which(name)
        if not binary:
            return None
        if name in {"air", "uv"}:
            output = self._run([binary, "--help"] if name == "air" else [binary, "format", "--help"], capture=True)
            if output is None or output.returncode != 0:
                return None
            if name == "air":
                first = output.stdout.decode("utf-8", errors="replace").split("\n")[0]
                if "R language" not in first or "formatter" not in first:
                    return None
        return [binary, *arguments]

    def _detect_project(self, name: str, arguments: tuple[str, ...]) -> list[str] | None:
        if name in {"prettier", "oxfmt", "pint"}:
            for path in self._find_up("composer.json" if name == "pint" else "package.json"):
                data = json.loads(path.read_text(encoding="utf-8"))
                if name == "pint":
                    if self._has_key(data, "require", "laravel/pint") or self._has_key(data, "require-dev", "laravel/pint"):
                        return ["./vendor/bin/pint", *arguments]
                elif self._has_key(data, "dependencies", name) or self._has_key(data, "devDependencies", name):
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
                    if found and (config != "pyproject.toml" or "[tool.ruff]" in self._read_text(found[0])):
                        return [binary, *arguments]
                for dependency in ("requirements.txt", "pyproject.toml", "Pipfile"):
                    found = self._find_up(dependency)
                    if found and "ruff" in self._read_text(found[0]):
                        return [binary, *arguments]
                return None
        return [binary, *arguments] if binary else None
