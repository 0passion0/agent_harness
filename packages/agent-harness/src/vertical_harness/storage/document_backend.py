"""Filesystem backend with text and Office document support."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from itertools import islice
from pathlib import Path
from typing import Any

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import EditResult, FileData, ReadResult, WriteResult
from deepagents.backends.utils import slice_read_response

from vertical_harness.bridge.attachments import AttachmentContent


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

    def __init__(self, *args: Any, **kwargs: Any) -> None:
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
            if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                return super().read(file_path, offset, limit)
            # 第五步：普通文本文件按行切片读取并做二进制安全校验
            with path.open(encoding="utf-8-sig") as stream:
                window = list(islice(stream, offset, offset + limit + 1))
            if not window and offset:
                return ReadResult(error=f"Line offset {offset} exceeds file length")
            more = len(window) > limit
            content = "".join(window[:limit])
            if "\0" in content:
                return ReadResult(error="Binary file: use a suitable Python library.")
            end = offset + min(limit, len(window))
            return ReadResult(
                file_data=FileData(content=content, encoding="utf-8"),
                start_line=offset + 1 if window else None,
                end_line=end if window else None,
                next_offset=end if more else None,
                total_lines=None if more else end,
            )
        except (OSError, ValueError) as exc:
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
            path = self._text_path(file_path)
            # 第二步：以 UTF-8 编码原子落盘写入文件
            self._atomic_write(path, content.encode("utf-8"))
            return WriteResult(path=file_path)
        except (OSError, ValueError) as exc:
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
            # 第一步：校验路径并读取原始文件字节流与 BOM
            path = self._text_path(file_path)
            original = path.read_bytes()
            text = original.decode("utf-8-sig")

            # 第二步：统一换行符归一化匹配，保持未变更行的原生换行符不受破坏
            normalized = text.replace("\r\n", "\n").replace("\r", "\n")
            old_string = old_string.replace("\r\n", "\n").replace("\r", "\n")
            new_string = new_string.replace("\r\n", "\n").replace("\r", "\n")
            if not old_string:
                return EditResult(error="old_string must not be empty")
            occurrences = normalized.count(old_string)
            if not occurrences:
                return EditResult(
                    error="String not found; read the current file and use an exact match."
                )
            if occurrences > 1 and not replace_all:
                return EditResult(
                    error=f"String appears {occurrences} times; provide unique surrounding context or replace_all=True."
                )

            # 第三步：构建字符偏移映射表以精准映射回原始换行格式
            positions = [0]
            i = 0
            while i < len(text):
                i += 2 if text[i: i + 2] == "\r\n" else 1
                positions.append(i)
            newline = "\r\n" if "\r\n" in text else "\r" if "\r" in text else "\n"
            replacement = new_string.replace("\n", newline)

            # 第四步：倒序执行文本局部替换，避免前序替换破坏后续位置偏移
            matches = list(re.finditer(re.escape(old_string), normalized))
            for match in reversed(matches):
                text = (
                        text[: positions[match.start()]] + replacement + text[positions[match.end()]:]
                )
            data = (b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b"") + text.encode(
                "utf-8"
            )

            # 第五步：原子写入更新后的文件数据并返回编辑结果
            self._atomic_write(path, data)
            return EditResult(path=file_path, occurrences=occurrences)
        except (OSError, ValueError) as exc:
            return EditResult(error=f"Edit failed: {exc}")
