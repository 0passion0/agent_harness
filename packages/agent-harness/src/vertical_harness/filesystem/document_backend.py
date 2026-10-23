"""Filesystem backend with text and Office document support."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import threading
from collections.abc import Callable
from difflib import unified_diff
from pathlib import Path
from typing import Any

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import EditResult, FileData, ReadResult, WriteResult
from deepagents.backends.utils import slice_read_response

from vertical_harness.bridge.attachments import AttachmentContent
from vertical_harness.filesystem.file_mutation import FileMutation
from vertical_harness.filesystem.code_formatter import CodeFormatter
from vertical_harness.filesystem.file_reader import FileReader
from vertical_harness.filesystem.patch_editor import PatchEditor
from vertical_harness.filesystem.ripgrep_search import RipgrepSearch
from vertical_harness.filesystem.text_editor import TextEditor


class DocumentFilesystemBackend(FilesystemBackend):
    """支持分页 UTF-8 读取、Office/PDF 文档提取及原子文本编辑的文件系统后端。

    继承自 DeepAgents FilesystemBackend，提供对多模态文本格式的透明读取与最近文档缓存，
    并确保工作区内文本写入与编辑操作具备原子性与编码安全性。

    :author: tongliga
    :date: 2026-10-21
    """

    _TEXT_DOCUMENTS = frozenset({".pdf", ".docx", ".xlsx", ".xlsm", ".pptx"})
    _SCRIPT_DOCUMENTS = frozenset(
        {".doc", ".ppt", ".pptm", ".docm", ".xls", ".xlsb", ".odt", ".ods", ".odp"}
    )

    def __init__(self, *args: Any, formatter: CodeFormatter | None = None, **kwargs: Any) -> None:
        """初始化文档文件系统后端实例。

        :param args: 传递给父类 FilesystemBackend 的位置参数。
        :type args: typing.Any
        :param kwargs: 传递给父类 FilesystemBackend 的关键字参数。
        :type kwargs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-21
        """
        super().__init__(*args, **kwargs)
        self._document_cache: tuple[tuple[Any, ...], str] | None = None
        self.search = RipgrepSearch(self.cwd, self._resolve_path)
        self.formatter = formatter

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """从工作区指定文件中分页读取 UTF-8 文本或提取 Office/PDF 文档内容。

        若文件为 Office/PDF 二进制文档，通过 MarkItDown 提取为 Markdown 并启用最近文档缓存；
        普通文本文件按行号分页读取并校验非二进制内容。

        :param file_path: 工作区内的相对或虚拟文件路径。
        :type file_path: str
        :param offset: 起始行偏移量（从 0 开始），负数自动归零。
        :type offset: int
        :param limit: 单次读取的最大行数上限，负数自动归零。
        :type limit: int
        :return: 包含读取文本内容、起止行号及分页标志的读取结果对象。
        :rtype: deepagents.backends.protocol.ReadResult
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：规范化偏移量与行数上限
        offset, limit = max(0, offset), max(0, limit)
        if not limit:
            return ReadResult(
                file_data=FileData(content="", encoding="utf-8"), no_lines_requested=True
            )
        try:
            # 第二步：解析路径并按文件扩展名分流处理策略
            path = self._resolve_path(file_path)
            suffix = path.suffix.lower()
            if suffix in self._SCRIPT_DOCUMENTS:
                return ReadResult(
                    error=f"{suffix} requires a suitable Python library; read_file does not parse it."
                )
            # 第三步：处理需提取 Markdown 的文档并维护 LRU 单项缓存
            if suffix in self._TEXT_DOCUMENTS:
                stat = path.stat()
                key = (str(path), stat.st_size, stat.st_mtime_ns)
                if self._document_cache is None or self._document_cache[0] != key:
                    self._document_cache = (key, AttachmentContent.extract_text(path))
                return slice_read_response(
                    FileData(content=self._document_cache[1], encoding="utf-8"), offset, limit
                )
            # 第四步：图片附件转交上层基类处理
            if media := FileReader.read_supported_media(path):
                return ReadResult(file_data=FileData(content=media[1], encoding="base64"))
            # 第五步：普通文本文件按行切片读取并做二进制安全校验
            return FileReader.read_text_page(path, offset, limit)
        except Exception as exc:
            return ReadResult(error=f"Cannot read file ({type(exc).__name__}).")

    def _text_path(self, file_path: str) -> Path:
        """执行工作区安全边界检查，拒绝 Office/PDF 或二进制文件的文本编辑请求。

        :param file_path: 目标相对或虚拟文件路径。
        :type file_path: str
        :return: 解析后的工作区物理 Path 对象。
        :rtype: pathlib.Path
        :raises ValueError: 当尝试以文本形式编辑 Office 文档或非 UTF-8 二进制文件时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：解析目标文件路径并定位真实物理文件
        path = self._resolve_path(file_path)
        # 第二步：断言拒绝 Office 与脚本类专用二进制格式作为纯文本编辑
        if path.suffix.lower() in self._TEXT_DOCUMENTS | self._SCRIPT_DOCUMENTS:
            raise ValueError("Office/PDF files must be edited with a suitable Python library")
        # 第三步：若文件已存在，校验内容是否包含空字节且能以 UTF-8 解码
        if path.exists():
            data = path.read_bytes()
            if b"\0" in data:
                raise ValueError("Binary files cannot be edited as text")
            data.decode("utf-8-sig")
        return path

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        """将完整字节数据原子性落盘写入目标路径；写入失败时完整保留原文件。

        :param path: 目标文件物理 Path 对象。
        :type path: pathlib.Path
        :param data: 待落盘的二进制字节流数据。
        :type data: bytes
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：确保父目录存在
        FileMutation.check_cancelled()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            # 第二步：在同级目录下创建临时文件并刷写与同步磁盘缓存
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # 第三步：继承原有文件的文件权限属性并执行原子性替换
            if path.exists():
                shutil.copymode(path, temporary)
            FileMutation.check_cancelled()
            os.replace(temporary, path)
        finally:
            # 第四步：若替换未成功发生，安全清理残留临时文件
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def write(self, file_path: str, content: str) -> WriteResult:
        """在工作区内原子写入完整的 UTF-8 文本文件。

        :param file_path: 目标工作区相对或虚拟文件路径。
        :type file_path: str
        :param content: 待写入的完整纯文本内容。
        :type content: str
        :return: 包含成功路径或错误信息的写入结果对象。
        :rtype: deepagents.backends.protocol.WriteResult
        :author: tongliga
        :date: 2026-10-21
        """
        try:
            # 第一步：校验文件路径合法性与纯文本兼容性
            path = self._resolve_path(file_path)
            with FileMutation.acquire([path]):
                self._text_path(file_path)
                bom = path.exists() and path.read_bytes().startswith(b"\xef\xbb\xbf")
                text = content.lstrip("\ufeff")
                data = (b"\xef\xbb\xbf" if bom or content.startswith("\ufeff") else b"")
                self._atomic_write(path, data + text.encode("utf-8"))
                if self.formatter is not None and self.formatter.file(path):
                    self._sync_text_bom(path, bool(data))
            return WriteResult(path=file_path)
        except Exception as exc:
            # 第三步：捕获异常并返回错误结果
            return WriteResult(error=f"Write failed: {exc}")

    def edit(
            self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        """精确匹配并替换文件局部内容，保持原有 BOM 标记与换行符格式不变。

        :param file_path: 目标工作区相对或虚拟文件路径。
        :type file_path: str
        :param old_string: 待被替换的目标原文本子串。
        :type old_string: str
        :param new_string: 替换后的新文本内容。
        :type new_string: str
        :param replace_all: 当匹配到多处时是否全部替换，默认为 False。
        :type replace_all: bool
        :return: 包含替换发生次数及文件路径的编辑结果对象。
        :rtype: deepagents.backends.protocol.EditResult
        :author: tongliga
        :date: 2026-10-21
        """
        try:
            path = self._resolve_path(file_path)
            with FileMutation.acquire([path]):
                self._text_path(file_path)
                original = path.read_bytes()
                source = original.decode("utf-8")
                # Preserve the existing backend's CR-only text editing contract.
                legacy_cr = "\r" in source and "\n" not in source and "\r" not in old_string + new_string
                if legacy_cr:
                    source = source.replace("\r", "\n")
                text, occurrences = TextEditor.replace_text_with_count(
                    source, old_string, new_string, replace_all
                )
                if legacy_cr:
                    text = text.replace("\n", "\r")
                self._atomic_write(path, text.encode("utf-8"))
                if self.formatter is not None:
                    if self.formatter.file(path):
                        self._sync_text_bom(path, text.startswith("\ufeff"))
                    else:
                        path.read_bytes().decode("utf-8-sig")
            return EditResult(path=file_path, occurrences=occurrences)
        except Exception as exc:
            return EditResult(error=f"Edit failed: {exc}")

    def glob(self, pattern: str, path: str | None = None):
        return self.search.glob(pattern, path)

    def _sync_text_bom(self, path: Path, bom: bool) -> str:
        data = path.read_bytes()
        text = data.decode("utf-8").lstrip("\ufeff")
        normalized = (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")
        if normalized != data:
            self._atomic_write(path, normalized)
        return text

    async def aglob(self, pattern: str, path: str | None = None):
        return await self.search.aglob(pattern, path)

    @staticmethod
    def _run_mutation(operation: Callable[..., Any], args: tuple[Any, ...], cancelled: threading.Event) -> Any:
        token = FileMutation.cancelled.set(cancelled)
        try:
            return operation(*args)
        finally:
            FileMutation.cancelled.reset(token)

    async def _amutate(self, operation: Callable[..., Any], *args: Any) -> Any:
        cancelled = threading.Event()

        worker = asyncio.create_task(asyncio.to_thread(self._run_mutation, operation, args, cancelled))
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

    def apply_patch(
        self, patch_text: str, authorize: Callable[[str], str | None]
    ) -> tuple[str, dict[str, Any]]:
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
        applied: list[dict[str, str]] = []
        files: list[dict[str, Any]] = []
        with FileMutation.acquire(paths):
            prepared = []
            updates: dict[Path, str] = {}
            for hunk in hunks:
                FileMutation.check_cancelled()
                for value in (hunk.path, hunk.move_path):
                    if value is not None and (error := authorize(value)):
                        raise ValueError(error)
                target = self._text_path(hunk.path)
                destination = self._text_path(hunk.move_path) if hunk.move_path else target
                if hunk.type == "add":
                    before, after = "", hunk.contents
                    if after and not after.endswith("\n"):
                        after += "\n"
                else:
                    before = updates.get(target) if hunk.type == "update" else None
                    if before is None:
                        before = target.read_bytes().decode("utf-8")
                    after = "" if hunk.type == "delete" else PatchEditor.derive_update(
                        hunk.path, hunk.chunks, before
                    )
                    if hunk.type == "update" and not hunk.move_path:
                        updates[target] = after
                prepared.append((hunk, target, destination, before, after.encode("utf-8")))
            for hunk, target, destination, _before, data in prepared:
                FileMutation.check_cancelled()
                try:
                    if hunk.type == "delete":
                        target.unlink()
                    else:
                        self._atomic_write(destination, data)
                        if hunk.move_path:
                            target.unlink()
                except OSError as exc:
                    completed = ", ".join(item["resource"] for item in applied)
                    detail = f". Completed before failure: {completed}" if completed else ""
                    if hunk.move_path and destination.exists():
                        detail += f". Move destination may have been written: {hunk.move_path}"
                    raise OSError(f"Failed to {hunk.type} {hunk.path}: {exc}{detail}") from exc
                applied.append({
                    "type": hunk.type,
                    "resource": hunk.move_path or hunk.path,
                    "target": str(destination),
                })
            final_contents: dict[Path, str] = {}
            for hunk, _target, destination, _before, _data in prepared:
                if hunk.type != "delete" and destination not in final_contents:
                    try:
                        current = destination.read_bytes().decode("utf-8")
                        final_contents[destination] = (
                            self._sync_text_bom(destination, current.startswith("\ufeff"))
                            if self.formatter is not None and self.formatter.file(destination)
                            else current
                        )
                    except OSError as exc:
                        completed = ", ".join(item["resource"] for item in applied)
                        raise OSError(f"Failed to read {destination}: {exc}. Completed before failure: {completed}") from exc
            for hunk, _target, destination, before, _data in prepared:
                after = "" if hunk.type == "delete" else final_contents[destination]
                old_lines, new_lines = before.lstrip("\ufeff").splitlines(True), after.lstrip("\ufeff").splitlines(True)
                diff = list(unified_diff(old_lines, new_lines, fromfile=hunk.path, tofile=hunk.move_path or hunk.path))
                files.append({
                    "file": hunk.move_path or hunk.path,
                    "patch": "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in diff),
                    "status": {"add": "added", "update": "modified", "delete": "deleted"}[hunk.type],
                    "additions": sum(line.startswith("+") for line in diff[2:]),
                    "deletions": sum(line.startswith("-") for line in diff[2:]),
                })
        lines = ["Success. Updated the following files:"]
        lines.extend(f"{ {'add': 'A', 'update': 'M', 'delete': 'D'}[item['type']]} {item['resource']}" for item in applied)
        return "\n".join(lines), {"applied": applied, "files": files}
