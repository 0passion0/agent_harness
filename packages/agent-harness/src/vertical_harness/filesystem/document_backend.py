"""Filesystem backend with text and Office document support."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from difflib import unified_diff
from pathlib import Path
from typing import Any

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import EditResult, FileData, GlobResult, ReadResult, WriteResult
from deepagents.backends.utils import slice_read_response

from vertical_harness.filesystem.code_formatter import CodeFormatter
from vertical_harness.filesystem.document_text import DocumentText
from vertical_harness.filesystem.file_mutation import FileMutation
from vertical_harness.filesystem.file_reader import FileReader
from vertical_harness.filesystem.patch_editor import PatchEditor
from vertical_harness.filesystem.patch_hunk import PatchHunk
from vertical_harness.filesystem.patch_plan import PatchPlan
from vertical_harness.filesystem.ripgrep_search import RipgrepSearch
from vertical_harness.filesystem.text_editor import TextEditor


class DocumentFilesystemBackend(FilesystemBackend):
    """支持分页 UTF-8 读取、Office/PDF 文档提取及原子文本编辑的文件系统后端。"""

    _TEXT_DOCUMENTS = frozenset({".pdf", ".docx", ".xlsx", ".xlsm", ".pptx"})
    _SCRIPT_DOCUMENTS = frozenset(
        {".doc", ".ppt", ".pptm", ".docm", ".xls", ".xlsb", ".odt", ".ods", ".odp"}
    )

    def __init__(self, *args: Any, formatter: CodeFormatter | None = None, **kwargs: Any) -> None:
        """初始化文档文件系统后端实例。"""
        super().__init__(*args, **kwargs)
        self._document_cache: tuple[tuple[Any, ...], str] | None = None
        self.search = RipgrepSearch(self.cwd, self._resolve_path)
        self.formatter = formatter

    def resolve_path(self, file_path: str) -> Path:
        """集中适配框架的虚拟路径解析，同时保留工作区边界校验。"""
        return self._resolve_path(file_path)

    def virtual_path(self, path: Path) -> str:
        return self._to_virtual_path(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """从工作区指定文件中分页读取 UTF-8 文本或提取 Office/PDF 文档内容。"""
        offset, limit = max(0, offset), max(0, limit)
        if not limit:
            return ReadResult(
                file_data=FileData(content="", encoding="utf-8"), no_lines_requested=True
            )
        try:
            path = self._resolve_path(file_path)
            suffix = path.suffix.lower()
            if suffix in self._SCRIPT_DOCUMENTS:
                return ReadResult(
                    error=f"{suffix} requires a suitable Python library; read_file does not parse it."
                )
            if suffix in self._TEXT_DOCUMENTS:
                stat = path.stat()
                key = (str(path), stat.st_size, stat.st_mtime_ns)
                if self._document_cache is None or self._document_cache[0] != key:
                    self._document_cache = (key, DocumentText.extract_text(path))
                return slice_read_response(
                    FileData(content=self._document_cache[1], encoding="utf-8"), offset, limit
                )
            if media := FileReader.read_supported_media(path):
                return ReadResult(file_data=FileData(content=media[1], encoding="base64"))
            return FileReader.read_text_page(path, offset, limit)
        except Exception as exc:
            return ReadResult(error=f"Cannot read file ({type(exc).__name__}).")

    def _text_path(self, file_path: str) -> Path:
        """执行工作区安全边界检查，拒绝 Office/PDF 或二进制文件的文本编辑请求。"""
        path = self._resolve_path(file_path)
        if path.suffix.lower() in self._TEXT_DOCUMENTS | self._SCRIPT_DOCUMENTS:
            raise ValueError("Office/PDF files must be edited with a suitable Python library")
        if path.exists():
            data = path.read_bytes()
            if b"\0" in data:
                raise ValueError("Binary files cannot be edited as text")
            data.decode("utf-8-sig")
        return path

    def write(self, file_path: str, content: str) -> WriteResult:
        """在工作区内原子写入完整的 UTF-8 文本文件。"""
        try:
            path = self._resolve_path(file_path)
            with FileMutation.acquire([path]):
                self._text_path(file_path)
                bom = path.exists() and path.read_bytes().startswith(b"\xef\xbb\xbf")
                text = content.lstrip("\ufeff")
                data = b"\xef\xbb\xbf" if bom or content.startswith("\ufeff") else b""
                FileMutation.atomic_write(path, data + text.encode("utf-8"))
                if self.formatter is not None and self.formatter.file(path):
                    self._sync_text_bom(path, bool(data))
            return WriteResult(path=file_path)
        except Exception as exc:
            return WriteResult(error=f"Write failed: {exc}")

    def edit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        """精确匹配并替换文件局部内容，保持原有 BOM 标记与换行符格式不变。"""
        try:
            path = self._resolve_path(file_path)
            with FileMutation.acquire([path]):
                self._text_path(file_path)
                original = path.read_bytes()
                source = original.decode("utf-8")
                # Preserve the existing backend's CR-only text editing contract.
                legacy_cr = (
                    "\r" in source and "\n" not in source and "\r" not in old_string + new_string
                )
                if legacy_cr:
                    source = source.replace("\r", "\n")
                text, occurrences = TextEditor.replace_text_with_count(
                    source, old_string, new_string, replace_all
                )
                if legacy_cr:
                    text = text.replace("\n", "\r")
                FileMutation.atomic_write(path, text.encode("utf-8"))
                if self.formatter is not None:
                    if self.formatter.file(path):
                        self._sync_text_bom(path, text.startswith("\ufeff"))
                    else:
                        path.read_bytes().decode("utf-8-sig")
            return EditResult(path=file_path, occurrences=occurrences)
        except Exception as exc:
            return EditResult(error=f"Edit failed: {exc}")

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self.search.glob(pattern, path)

    def _sync_text_bom(self, path: Path, bom: bool) -> str:
        data = path.read_bytes()
        text = data.decode("utf-8").lstrip("\ufeff")
        normalized = (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")
        if normalized != data:
            FileMutation.atomic_write(path, normalized)
        return text

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return await self.search.aglob(pattern, path)

    @staticmethod
    def _run_mutation(
        operation: Callable[..., Any], args: tuple[Any, ...], cancelled: threading.Event
    ) -> Any:
        token = FileMutation.cancelled.set(cancelled)
        try:
            return operation(*args)
        finally:
            FileMutation.cancelled.reset(token)

    async def _amutate(self, operation: Callable[..., Any], *args: Any) -> Any:
        cancelled = threading.Event()

        worker = asyncio.create_task(
            asyncio.to_thread(self._run_mutation, operation, args, cancelled)
        )
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancelled.set()
            # A cancelled tool must not leave a worker writing after it returns.
            completion = asyncio.gather(worker, return_exceptions=True)
            while not completion.done():
                try:
                    await asyncio.shield(completion)
                except asyncio.CancelledError:
                    continue
            raise

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return await self._amutate(self.write, file_path, content)

    async def aedit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        return await self._amutate(self.edit, file_path, old_string, new_string, replace_all)

    async def aapply_patch(
        self, patch_text: str, authorize: Callable[[str], str | None]
    ) -> tuple[str, dict[str, Any]]:
        return await self._amutate(self.apply_patch, patch_text, authorize)

    def _prepare_patch(
        self,
        hunks: list[PatchHunk],
        authorize: Callable[[str], str | None],
    ) -> PatchPlan:
        plan = PatchPlan()
        for hunk in hunks:
            FileMutation.check_cancelled()
            for name in (hunk.path, hunk.move_path):
                if name is not None and (error := authorize(name)):
                    raise ValueError(error)
            target = self._text_path(hunk.path)
            destination = self._text_path(hunk.move_path) if hunk.move_path else target
            for path, name in ((target, hunk.path), (destination, hunk.move_path or hunk.path)):
                if path not in plan.original:
                    content = path.read_bytes().decode("utf-8") if path.exists() else None
                    plan.original[path] = content
                    plan.contents[path] = content
                    plan.names[path] = name
            before = plan.contents[target]
            if hunk.type == "add":
                after = hunk.contents
                if after and not after.endswith("\n"):
                    after += "\n"
            else:
                if before is None:
                    raise FileNotFoundError(f"File does not exist: {hunk.path}")
                after = (
                    ""
                    if hunk.type == "delete"
                    else PatchEditor.derive_update(hunk.path, hunk.chunks, before)
                )
            data = after.encode("utf-8")
            plan.contents[target] = None
            if hunk.type != "delete":
                plan.contents[destination] = after
            plan.operations.append((hunk, target, destination, data))
        return plan

    @staticmethod
    def _commit_patch(plan: PatchPlan) -> list[dict[str, str]]:
        applied: list[dict[str, str]] = []
        for hunk, target, destination, data in plan.operations:
            FileMutation.check_cancelled()
            try:
                if hunk.type == "delete":
                    target.unlink()
                else:
                    FileMutation.atomic_write(destination, data)
                    if hunk.move_path and destination != target:
                        target.unlink()
            except OSError as exc:
                completed = ", ".join(item["resource"] for item in applied)
                detail = f". Completed before failure: {completed}" if completed else ""
                if hunk.move_path and destination.exists():
                    detail += f". Move destination may have been written: {hunk.move_path}"
                raise OSError(f"Failed to {hunk.type} {hunk.path}: {exc}{detail}") from exc
            applied.append(
                {
                    "type": hunk.type,
                    "resource": hunk.move_path or hunk.path,
                    "target": str(destination),
                }
            )
        return applied

    def _patch_results(
        self,
        plan: PatchPlan,
        applied: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        for path, expected in plan.contents.items():
            before = plan.original[path]
            after = None
            if expected is not None:
                try:
                    if self.formatter is not None and self.formatter.file(path):
                        self._sync_text_bom(path, expected.startswith("\ufeff"))
                    after = path.read_bytes().decode("utf-8")
                except (OSError, UnicodeError) as exc:
                    completed = ", ".join(item["resource"] for item in applied)
                    raise OSError(
                        f"Failed to read {path}: {exc}. Completed before failure: {completed}"
                    ) from exc
            if before == after:
                continue
            name = plan.names[path]
            old_lines = (before or "").lstrip("\ufeff").splitlines(True)
            new_lines = (after or "").lstrip("\ufeff").splitlines(True)
            diff = list(unified_diff(old_lines, new_lines, fromfile=name, tofile=name))
            files.append(
                {
                    "file": name,
                    "patch": "".join(
                        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                        for line in diff
                    ),
                    "status": "added"
                    if before is None
                    else "deleted"
                    if after is None
                    else "modified",
                    "additions": sum(line.startswith("+") for line in diff[2:]),
                    "deletions": sum(line.startswith("-") for line in diff[2:]),
                }
            )
        return files

    def apply_patch(
        self,
        patch_text: str,
        authorize: Callable[[str], str | None],
    ) -> tuple[str, dict[str, Any]]:
        """先完整预检再按顺序提交；多文件 I/O 失败可能保留之前成功的操作。"""
        if not patch_text:
            raise ValueError("patchText is required")
        hunks = PatchEditor.parse_patch(patch_text)
        if not hunks:
            raise ValueError("patch rejected: empty patch")
        paths = [
            self._resolve_path(value)
            for hunk in hunks
            for value in (hunk.path, hunk.move_path)
            if value is not None
        ]
        with FileMutation.acquire(paths):
            plan = self._prepare_patch(hunks, authorize)
            applied = self._commit_patch(plan)
            files = self._patch_results(plan, applied)
        lines = ["Success. Updated the following files:"]
        lines.extend(
            f"{ {'add': 'A', 'update': 'M', 'delete': 'D'}[item['type']] } {item['resource']}"
            for item in applied
        )
        return "\n".join(lines), {"applied": applied, "files": files}
