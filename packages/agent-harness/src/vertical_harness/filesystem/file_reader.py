"""工作区文件安全分页读取、编码规范化与二进制/多模态探查模块。

本模块提供安全文件读取器 `FileReader`，负责：
1. 文本文件安全分页切片（限制 2,000 行、50 KiB 字节上限，截断 2,000 字符超长单行），防止超大文件溢出上下文窗口；
2. 跨平台文件名编码规范化（Unicode NFC 归一化，替换非断空格与弯引号）；
3. 多模态媒体文件二进制嗅探（自动解析 PNG、JPEG、GIF、WebP 及可选 PDF 幻数并转为 Base64 编码）；
4. 文件缺失时基于模糊匹配与权限检查给出候选修复建议。
"""

from __future__ import annotations

import base64
import os
import posixpath
import unicodedata
from collections.abc import Callable
from pathlib import Path

from deepagents.backends.protocol import FileData, ReadResult

# 单次 read_file 分页读取的最大行数上限
MAX_READ_LINES: int = 2000
# 单次文本分页读取的最大字节数上限（50 KiB），保护模型输入窗口
MAX_READ_BYTES: int = 50 * 1024
# 单行文本最大字符截断长度，防止超长单行（如 minified JS）撑爆显示
MAX_LINE_LENGTH: int = 2000
# 多模态媒体（图片与直接读取的 PDF）单文件字节上限（20 MiB）
MAX_MEDIA_INGEST_BYTES: int = 20 * 1024 * 1024


class FileReader:
    """工作区文件分页读取器，支持受限文本切片、路径歧义容错与原生多模态媒体嗅探。

    纯静态工具类，为 `DocumentFilesystemBackend` 提供稳健的底层文件读取支持。
    """

    @staticmethod
    def canonical(name: str) -> str:
        """对文件名执行 Unicode NFC 归一化并替换常见特殊空白与弯引号。

        将 macOS/Linux 下可能产生的 NFD 变音字符统一转换为标准 NFC 格式，
        并将非断空格（`\u00a0`, `\u202f`）转换为空格，弯引号转换为直引号以提高匹配兼容性。

        Args:
            name (str): 待归一化的原始文件名或路径字符串。

        Returns:
            str: 规范化转换后的纯净字符串。
        """
        return unicodedata.normalize("NFC", name).translate(
            {ord("\u00a0"): " ", ord("\u202f"): " ", ord("\u2018"): "'", ord("\u2019"): "'"}
        )

    @staticmethod
    def read_supported_media(path: Path, *, pdf: bool = False) -> tuple[str, str] | None:
        """检查文件二进制魔数并提取支持的图片或 PDF 数据为 Base64 编码。

        仅通过文件头前 12 字节校验真实文件格式（PNG, JPEG, GIF, WebP, PDF），
        不依赖不可靠的文件后缀名。当文件大小超过 20 MiB 限制时抛出异常。

        Args:
            path (Path): 待嗅探读取的目标物理文件绝对路径。
            pdf (bool): 是否允许直接按二进制原样读取 PDF 格式（若为 False 则仅支持图片）。

        Returns:
            tuple[str, str] | None: 匹配支持的媒体时返回 `(mime_type, base64_payload)` 二元组；
                若不是支持的媒体格式则返回 None。

        Raises:
            ValueError: 当文件体积超出 20 MiB 硬上限时抛出。
        """
        with path.open("rb") as stream:
            prefix = stream.read(12)
            if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            elif prefix.startswith(b"\xff\xd8\xff"):
                mime = "image/jpeg"
            elif prefix.startswith(b"GIF8"):
                mime = "image/gif"
            elif prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
                mime = "image/webp"
            elif pdf and prefix.startswith(b"%PDF-"):
                mime = "application/pdf"
            else:
                return None
            if os.fstat(stream.fileno()).st_size > MAX_MEDIA_INGEST_BYTES:
                raise ValueError(f"Media exceeds {MAX_MEDIA_INGEST_BYTES} byte limit")
            stream.seek(0)
            content = stream.read(MAX_MEDIA_INGEST_BYTES + 1)
            if len(content) > MAX_MEDIA_INGEST_BYTES:
                raise ValueError(f"Media exceeds {MAX_MEDIA_INGEST_BYTES} byte limit")
        return mime, base64.b64encode(content).decode("ascii")

    @staticmethod
    def recover_read_path(root: Path, requested: Path) -> Path | None:
        """当指定文件不存在时，在同目录下尝试通过 Unicode 规范化名称寻址唯一匹配文件。

        应对因大小写或 Unicode 编码分解导致的路径寻找失败；若找到唯一合法匹配且在工作区内，则自动纠正寻址。

        Args:
            root (Path): 工作区绝对物理根路径。
            requested (Path): 原始请求但物理不存在的文件路径。

        Returns:
            Path | None: 成功纠正且在工作区范围内的真实文件绝对路径；若无匹配或存在多项歧义则返回 None。
        """
        base = FileReader.canonical(requested.name)
        try:
            with os.scandir(requested.parent) as entries:
                matches = [
                    requested.parent / entry.name
                    for entry in entries
                    if entry.is_file(follow_symlinks=False)
                    and FileReader.canonical(entry.name) == base
                ]
            if len(matches) != 1:
                return None
            candidate = matches[0].resolve()
            return candidate if candidate.is_relative_to(root.resolve()) else None
        except OSError:
            return None

    @staticmethod
    def missing_read_message(
        requested_virtual: str,
        requested: Path,
        authorize: Callable[[str], str | None] | None = None,
    ) -> str:
        """生成友好的文件未找到提示信息，并在同目录下附带最多 3 个模糊建议路径。

        遍历所在目录下的现有文件，按子串包含关系提供智能建议，并过滤掉无权访问的敏感文件。

        Args:
            requested_virtual (str): 用户请求读取的虚拟相对路径（如 `"/docs/readme.txt"`）。
            requested (Path): 对应的物理路径对象。
            authorize (Callable[[str], str | None] | None): 权限校验探针，用于过滤掉未授权的备选路径。

        Returns:
            str: 格式化后的错误提示文本，包含可能的候选文件列表。
        """
        base = posixpath.basename(requested_virtual).lower()
        try:
            suggestions = [
                posixpath.join(posixpath.dirname(requested_virtual), entry.name)
                for entry in requested.parent.iterdir()
                if base in entry.name.lower() or entry.name.lower() in base
                if authorize is None
                or not authorize(posixpath.join(posixpath.dirname(requested_virtual), entry.name))
            ][:3]
        except OSError:
            suggestions = []
        message = f"File not found: {requested_virtual}"
        if suggestions:
            message += "\n\nDid you mean one of these?\n" + "\n".join(suggestions)
        return message

    @staticmethod
    def directory_entries(path: Path) -> list[tuple[str, bool]]:
        """列出指定目录下的可见条目名称与其是否为目录的布尔标记，保证确定性排序。

        使用 `os.scandir` 高效扫描，过滤掉破损链接与不可读文件。
        目录优先排在前面，同类型条目按字典序升序排列。

        Args:
            path (Path): 待扫描的物理目录路径。

        Returns:
            list[tuple[str, bool]]: 二元组列表 `[(name, is_dir), ...]`，`is_dir` 为 True 表示目录。
        """
        with os.scandir(path) as entries:
            visible = [
                (entry.name, entry.is_dir(follow_symlinks=False))
                for entry in entries
                if entry.is_dir(follow_symlinks=False)
                or entry.is_file(follow_symlinks=False)
                or entry.is_symlink()
            ]
        # 目录优先排在前，文件名按 Python 字典序升序排列
        return sorted(visible, key=lambda entry: (not entry[1], entry[0]))

    @staticmethod
    def read_text_page(path: Path, offset: int = 0, limit: int = 2000) -> ReadResult:
        """分页受控读取文本文件切片，严格限制最大行数、最大字节数与超长单行截断。

        流式解析文件，检测并拒绝包含空字符 `\0` 的二进制文件。
        遵循保护窗口策略：单次最多 2,000 行、单次最多 50 KiB 字节、单行最多 2,000 字符。

        Args:
            path (Path): 待读取的本地物理文件路径。
            offset (int): 从 0 开始的起始行偏移量（跳过前 offset 行），默认 0。
            limit (int): 本次最多读取的文本行数，默认 2,000（受限于 MAX_READ_LINES）。

        Returns:
            ReadResult: 包含以下字段的读取结果对象：
                - `file_data` (FileData): 包含解码后的文本内容与编码格式（utf-8）；
                - `start_line` (int | None): 读取的 1-based 起始行号；
                - `end_line` (int | None): 读取的 1-based 结束行号；
                - `next_offset` (int | None): 若文件后续仍有内容，则为下一页的行偏移量；
                - `total_lines` (int | None): 若已读取至文件末尾，则为文件总行数；
                - `error` (str | None): 若读取失败（如二进制文件或行偏移越界），则为错误原因描述。
        """
        offset, limit = max(0, offset), min(MAX_READ_LINES, max(0, limit))
        if not limit:
            return ReadResult(
                file_data=FileData(content="", encoding="utf-8"), no_lines_requested=True
            )

        selected: list[str] = []
        size = 0
        index = 0
        more = False
        with path.open(encoding="utf-8-sig") as stream:
            while True:
                prefix = b""
                length = 0
                binary = False
                ending = ""
                while part := stream.readline(8192):
                    binary = binary or "\0" in part
                    text = part[:-1] if part.endswith("\n") else part
                    units = text.encode("utf-16-le")
                    length += len(units) // 2
                    if len(prefix) < MAX_LINE_LENGTH * 2:
                        prefix += units[: MAX_LINE_LENGTH * 2 - len(prefix)]
                    if part.endswith("\n"):
                        ending = "\n"
                        break
                if not part and not length:
                    break
                if index >= offset and len(selected) >= limit:
                    more = True
                    break
                if binary and index < offset:
                    return ReadResult(error="Binary file: use a suitable Python library.")
                if index >= offset:
                    line = (
                        prefix.decode("utf-16-le", errors="surrogatepass")
                        + ("... (line truncated to 2000 chars)" if length > MAX_LINE_LENGTH else "")
                        + ending
                    )
                    line_size = len(line.encode("utf-8", errors="surrogatepass"))
                    if size + line_size > MAX_READ_BYTES:
                        more = True
                        break
                    if binary:
                        return ReadResult(error="Binary file: use a suitable Python library.")
                    selected.append(line)
                    size += line_size
                index += 1
        if not selected and offset and not more:
            return ReadResult(error=f"Line offset {offset} exceeds file length")
        end = offset + len(selected)
        return ReadResult(
            file_data=FileData(content="".join(selected), encoding="utf-8"),
            start_line=offset + 1 if selected else None,
            end_line=end if selected else None,
            next_offset=end if more else None,
            total_lines=None if more or not selected else end,
        )
