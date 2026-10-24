"""具备现代 Office 文档解析与高保真代码编辑能力的文件系统沙箱后端模块。

本模块提供核心后端实现 `DocumentFilesystemBackend`，继承自 DeepAgents 的 `FilesystemBackend`：
1. 扩展阅读支持：自动识别 PDF、Word (.docx)、Excel (.xlsx)、PowerPoint (.pptx) 等现代文档，
   并利用 MarkItDown 动态转录为 Markdown 结构化文本；原生支持 PNG、JPEG 等图片的 Base64 读取；
2. 文本原子编辑：借助 `FileMutation` 实现文件级重入锁与原子落盘，通过 `TextEditor` 进行多层级容错局部替换；
3. 外部代码格式化联动：在文件发生写或编辑操作后，自动触发配套的 `CodeFormatter` 进行就地美化，并同步维护原有 BOM 标记；
4. 复合补丁应用与统一差异报告：支持多文件两阶段原子修补（`apply_patch`），生成标准 `unified_diff` 审计快照；
5. 异步与取消保护：所有涉及写入的操作均通过 `_amutate` 在工作线程中隔离执行，支持协同取消且保证写入安全退出。
"""

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
    """支持分页 UTF-8 读取、Office/PDF 文档提取及原子文本编辑的文件系统后端。

    Attributes:
        search (RipgrepSearch): 绑定的 Ripgrep 搜索执行器实例。
        formatter (CodeFormatter | None): 可选的外部代码自动格式化器。
    """

    # 支持通过 MarkItDown 提取纯文本/Markdown 的现代 Office 与 PDF 文档后缀集合
    _TEXT_DOCUMENTS: frozenset[str] = frozenset({".pdf", ".docx", ".xlsx", ".xlsm", ".pptx"})
    # 需由智能体编写并运行专属 Python 脚本处理的遗留或宏二进制文档后缀集合
    _SCRIPT_DOCUMENTS: frozenset[str] = frozenset(
        {".doc", ".ppt", ".pptm", ".docm", ".xls", ".xlsb", ".odt", ".ods", ".odp"}
    )

    def __init__(self, *args: Any, formatter: CodeFormatter | None = None, **kwargs: Any) -> None:
        """初始化文档文件系统后端实例。

        Args:
            *args (Any): 传递给基类 `FilesystemBackend` 的位置参数（如 root_dir 等）。
            formatter (CodeFormatter | None): 可选的代码自动格式化管理器。
            **kwargs (Any): 传递给基类的关键字参数（如 virtual_mode 等）。
        """
        super().__init__(*args, **kwargs)
        self._document_cache: tuple[tuple[Any, ...], str] | None = None
        self.search = RipgrepSearch(self.cwd, self._resolve_path)
        self.formatter = formatter

    def resolve_path(self, file_path: str) -> Path:
        """集中适配框架的虚拟路径解析，同时保留工作区边界校验。

        Args:
            file_path (str): 虚拟工作区相对或绝对路径字符串。

        Returns:
            Path: 解析后的本地绝对物理路径。
        """
        return self._resolve_path(file_path)

    def virtual_path(self, path: Path) -> str:
        """将物理文件路径转换为带有前导斜杠的工作区标准虚拟路径。

        Args:
            path (Path): 工作区内部的物理文件路径。

        Returns:
            str: 格式如 `"/src/main.py"` 的规范化虚拟路径字符串。
        """
        return self._to_virtual_path(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """从工作区指定文件中分页读取 UTF-8 文本或提取 Office/PDF 文档内容。

        根据文件后缀名与二进制魔数分别路由：
        - 遗留二进制 Office 文档（.doc, .xls 等）：拒绝直接读取，提示编写 Python 脚本；
        - 现代文档（.pdf, .docx, .xlsx 等）：通过 MarkItDown 提取为 Markdown 并缓存；
        - 支持的图片格式（PNG, JPEG, GIF, WebP）：读取并输出为 Base64 编码载荷；
        - 普通纯文本文件：受控分页读取切片，限制 2,000 行 / 50 KiB。

        Args:
            file_path (str): 目标文件的虚拟相对路径。
            offset (int): 起始读取行号偏移（0-based），默认 0。
            limit (int): 本次读取的最大行数配额，默认 2,000。

        Returns:
            ReadResult: 结构化读取结果，包含内容、起止行号或格式化错误原因。
        """
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
        """执行工作区安全边界检查，拒绝 Office/PDF 或二进制文件的文本编辑请求。

        Args:
            file_path (str): 待检查的目标文件相对路径。

        Returns:
            Path: 解析后的有效文本文件绝对物理路径。

        Raises:
            ValueError: 若目标是 Office/PDF 文档或探测到二进制空字符 `\0` 时抛出。
        """
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
        """在工作区内原子写入完整的 UTF-8 文本文件。

        自动获取文件级排他锁，进行安全类型检查，保留既有的 BOM 标记，并触发外部格式化器。

        Args:
            file_path (str): 写入的目标文件虚拟路径。
            content (str): 准备全量写入的 UTF-8 文本。

        Returns:
            WriteResult: 包含写入文件路径或失败错误信息的写入结果对象。
        """
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
        """精确匹配并替换文件局部内容，保持原有 BOM 标记与换行符格式不变。

        Args:
            file_path (str): 目标编辑文件相对路径。
            old_string (str): 待定位的原字符串。
            new_string (str): 替换目标新字符串。
            replace_all (bool): 是否替换所有匹配项，默认 False。

        Returns:
            EditResult: 包含文件路径与实际匹配替换次数的结果对象；若失败则包含错误信息。
        """
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
        """在指定工作区目录下按通配符模式检索匹配的文件路径列表。

        Args:
            pattern (str): 文件通配模式（如 `"**/*.ts"`）。
            path (str | None): 可选的限定搜索相对目录。

        Returns:
            GlobResult: 匹配的文件列表结果。
        """
        return self.search.glob(pattern, path)

    def _sync_text_bom(self, path: Path, bom: bool) -> str:
        """格式化后同步维持原始文本文件的 UTF-8 BOM 标记状态。

        Args:
            path (Path): 目标物理文件绝对路径。
            bom (bool): 是否应该保留 UTF-8 BOM 标记。

        Returns:
            str: 规范化去除 BOM 后的文本字符串。
        """
        data = path.read_bytes()
        text = data.decode("utf-8").lstrip("\ufeff")
        normalized = (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")
        if normalized != data:
            FileMutation.atomic_write(path, normalized)
        return text

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """以异步非阻塞方式在指定工作区目录下检索匹配的文件路径列表。

        Args:
            pattern (str): 文件通配表达式。
            path (str | None): 可选限定搜索目录。

        Returns:
            GlobResult: 匹配结果对象。
        """
        return await self.search.aglob(pattern, path)

    @staticmethod
    def _run_mutation(
        operation: Callable[..., Any], args: tuple[Any, ...], cancelled: threading.Event
    ) -> Any:
        """在工作线程中绑定协同取消探针并执行文件修改操作。

        Args:
            operation (Callable[..., Any]): 待执行的同步写入函数。
            args (tuple[Any, ...]): 传递给函数的参数元组。
            cancelled (threading.Event): 关联的取消信号事件。

        Returns:
            Any: 操作执行后的返回值。
        """
        token = FileMutation.cancelled.set(cancelled)
        try:
            return operation(*args)
        finally:
            FileMutation.cancelled.reset(token)

    async def _amutate(self, operation: Callable[..., Any], *args: Any) -> Any:
        """异步包装文件修改操作，支持协同取消并在取消时等待写线程安全退出。

        Args:
            operation (Callable[..., Any]): 目标修改函数。
            *args (Any): 传递给该函数的参数序列。

        Returns:
            Any: 修改函数的同步返回结果。

        Raises:
            asyncio.CancelledError: 若异步任务在写操作完成前被外部取消。
        """
        cancelled = threading.Event()

        worker = asyncio.create_task(
            asyncio.to_thread(self._run_mutation, operation, args, cancelled)
        )
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancelled.set()
            # 已取消的工具不可在后台残留继续写入，必须等待工作线程退出后方可抛出取消
            completion = asyncio.gather(worker, return_exceptions=True)
            while not completion.done():
                try:
                    await asyncio.shield(completion)
                except asyncio.CancelledError:
                    continue
            raise

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """以异步协同取消方式在工作区内原子写入完整文本文件。

        Args:
            file_path (str): 目标虚拟路径。
            content (str): 写入文本。

        Returns:
            WriteResult: 写入操作结果。
        """
        return await self._amutate(self.write, file_path, content)

    async def aedit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        """以异步协同取消方式对工作区文件局部内容执行精确定位替换。

        Args:
            file_path (str): 目标虚拟路径。
            old_string (str): 待替换原字符串。
            new_string (str): 置换的新文本。
            replace_all (bool): 是否全量替换。

        Returns:
            EditResult: 编辑操作结果。
        """
        return await self._amutate(self.edit, file_path, old_string, new_string, replace_all)

    async def aapply_patch(
        self, patch_text: str, authorize: Callable[[str], str | None]
    ) -> tuple[str, dict[str, Any]]:
        """以异步协同取消方式对工作区应用复合差异补丁。

        Args:
            patch_text (str): 补丁正文。
            authorize (Callable[[str], str | None]): 路径安全访问鉴权探针。

        Returns:
            tuple[str, dict[str, Any]]: 格式化响应摘要文本及结构化文件变更元数据字典。
        """
        return await self._amutate(self.apply_patch, patch_text, authorize)

    def _prepare_patch(
        self,
        hunks: list[PatchHunk],
        authorize: Callable[[str], str | None],
    ) -> PatchPlan:
        """预检全量补丁变更：校验权限、模拟内存应用并构建完整的提交计划。

        Args:
            hunks (list[PatchHunk]): 待预检的补丁分块对象列表。
            authorize (Callable[[str], str | None]): 权限校验回调。

        Returns:
            PatchPlan: 预检完成的补丁执行计划实体。

        Raises:
            ValueError: 权限校验未通过时抛出。
            FileNotFoundError: 待修改的文件物理不存在时抛出。
        """
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
        """按顺序提交补丁物理写入与删除；若失败在异常中准确报告已提交的操作列表。

        Args:
            plan (PatchPlan): 已预检通过的补丁执行计划。

        Returns:
            list[dict[str, str]]: 成功提交的文件操作记录列表。

        Raises:
            OSError: 物理磁盘写入或删除失败时抛出。
        """
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
        """格式化受影响文件并比对原始快照，生成结构化的 unified_diff 差异报告。

        Args:
            plan (PatchPlan): 执行计划对象。
            applied (list[dict[str, str]]): 已提交的操作列表。

        Returns:
            list[dict[str, Any]]: 结构化受影响文件变更列表，包含补丁 diff 文本、新增行与删除行统计。

        Raises:
            OSError: 读取已修改文件失败时抛出。
        """
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
        """先完整预检再按顺序提交；多文件 I/O 失败可能保留之前成功的操作。

        Args:
            patch_text (str): 复合补丁原始文本。
            authorize (Callable[[str], str | None]): 安全权限拦截检查回调。

        Returns:
            tuple[str, dict[str, Any]]: 包含操作结果汇总行与变更明细字典的二元组：
                - `summary` (str): 如 `"Success. Updated the following files:\nA file.py"`；
                - `payload` (dict): `{"applied": [...], "files": [...]}`。

        Raises:
            ValueError: 补丁文本为空、语法非法或权限拒绝时抛出。
            OSError: 底层物理文件提交失败时抛出。
        """
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
