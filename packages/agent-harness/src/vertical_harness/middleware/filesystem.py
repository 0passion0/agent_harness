"""工作区文件系统操作中间件，集成安全分页读取、多模态媒体嗅探、增强通配检索与原子补丁应用。

本模块提供 `HarnessFilesystemMiddleware` 中间件，继承自 DeepAgents 的 `FilesystemMiddleware`：
1. 挂载增强型工具集：扩展 `read_file`（支持目录分页与多模态二进制直传）、`grep`（支持 ripgrep 字面量/正则与结构化行元数据）、
   `glob`（支持隐藏文件过滤与上限限制），并附加原子多文件补丁工具 `apply_patch`；
2. 全局技能只读挂载：通过 `skill_backend` 拦截 `/.agent-harness/skills/` 虚拟路径的只读访问；
3. 动态子目录 AGENTS.md 嗅探注入：在 `before_model` 钩子中，根据最近已读文件所在的目录层级，
   自底向上探查同级或父级目录中的 `AGENTS.md` 并注入会话提示，实现精准的作用域规范感知；
4. 权限与容错集成：Unicode 文件名歧义自愈、读写权限白名单校验、多文件事务提交报告生成。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from deepagents.backends.protocol import BackendProtocol, GlobResult, GrepResult
from deepagents.backends.utils import validate_path
from deepagents.middleware.filesystem import (
    FilesystemMiddleware,
    _apply_permissions_to_glob_results,
    _filter_grep_matches_by_permission,
    _format_glob_tool_result,
    _format_grep_tool_result,
    _permission_error,
)
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.content import create_file_block, create_image_block
from langchain_core.tools import StructuredTool

from vertical_harness.filesystem.document_backend import DocumentFilesystemBackend
from vertical_harness.filesystem.file_reader import FileReader
from vertical_harness.filesystem.structured_grep_result import StructuredGrepResult
from vertical_harness.middleware.file_glob_schema import FileGlobSchema
from vertical_harness.middleware.native_read_schema import NativeReadSchema
from vertical_harness.middleware.patch_schema import PatchSchema
from vertical_harness.middleware.search_schema import SearchSchema


class HarnessFilesystemMiddleware(FilesystemMiddleware):
    """强化型文件系统操作拦截中间件，提供多文件补丁、Ripgrep 结构化搜索与分页阅读。

    Attributes:
        backend (DocumentFilesystemBackend): 绑定的沙箱文件系统后端。
    """

    backend: DocumentFilesystemBackend

    def __init__(
        self,
        *,
        backend: DocumentFilesystemBackend,
        skill_backend: BackendProtocol | None = None,
        **kwargs: Any,
    ) -> None:
        """初始化 HarnessFilesystemMiddleware，附加原子多文件补丁工具与全局技能只读后端。

        Args:
            backend (DocumentFilesystemBackend): 沙箱文件系统后端。
            skill_backend (BackendProtocol | None): 全局技能存储后端，若提供则挂载技能文件的只读拦截路由。
            **kwargs (Any): 传递给基类 `FilesystemMiddleware` 的参数。
        """
        super().__init__(backend=backend, **kwargs)
        self._skills_read: Callable[..., ToolMessage] | None = None
        self._skills_aread: Callable[..., Awaitable[ToolMessage]] | None = None
        if skill_backend is not None:
            tool = FilesystemMiddleware(
                backend=skill_backend,
                tools=["read_file"],
                _permissions=self._permissions,
                tool_token_limit_before_evict=self._tool_token_limit_before_evict,
            ).tools[0]
            assert (
                isinstance(tool, StructuredTool)
                and tool.func is not None
                and tool.coroutine is not None
            )
            self._skills_read, self._skills_aread = tool.func, tool.coroutine
        self.tools = [*self.tools, self._create_patch_tool()]

    def _authorize(self, operation: Literal["read", "write"], path: str) -> str | None:
        """校验当前路径与操作类型是否在工作区权限许可范围内。

        Args:
            operation (Literal["read", "write"]): 操作类型。
            path (str): 目标路径字符串。

        Returns:
            str | None: 若被权限规则拒绝则返回错误描述文本，否则返回 None。
        """
        try:
            return _permission_error(
                self._permissions, operation, self.backend, validate_path(path)
            )
        except ValueError as exc:
            return str(exc)

    def _read_path(self, file_path: str) -> tuple[str, str | None]:
        """安全解析并校验读路径，支持 Unicode 规范化名称自动容错恢复。

        Args:
            file_path (str): 请求的虚拟文件路径。

        Returns:
            tuple[str, str | None]: 二元组 `(resolved_path, error_message)`。
        """
        if error := self._authorize("read", file_path):
            return file_path, error
        validated = validate_path(file_path)
        try:
            path = self.backend.resolve_path(validated)
            if path.exists():
                return validated, None
            alternate = FileReader.recover_read_path(self.backend.cwd, path)
            if alternate is None:
                return validated, FileReader.missing_read_message(
                    validated, path, lambda candidate: self._authorize("read", candidate)
                )
            recovered = self.backend.virtual_path(alternate)
            return recovered, self._authorize("read", recovered)
        except Exception as exc:
            return validated, f"Cannot read file ({type(exc).__name__}: {exc})."

    def _read_directory(
        self, file_path: str, offset: int, limit: int, runtime: ToolRuntime[Any]
    ) -> ToolMessage:
        """安全分页读取目录下的直接子文件与子目录列表。

        Args:
            file_path (str): 目录路径。
            offset (int): 起始行偏移量。
            limit (int): 最大条目限制配额。
            runtime (ToolRuntime[Any]): 工具运行时上下文。

        Returns:
            ToolMessage: 格式化后的目录清单消息。
        """
        try:
            path = self.backend.resolve_path(file_path)
            entries = [
                name + ("/" if is_dir else "")
                for name, is_dir in FileReader.directory_entries(path)
                if not self._authorize("read", file_path.rstrip("/") + "/" + name)
            ]
        except Exception as exc:
            return ToolMessage(
                content=f"Cannot read directory ({type(exc).__name__}): {exc}",
                status="error",
                name="read_file",
                tool_call_id=runtime.tool_call_id,
            )
        offset, limit = max(0, offset), min(2000, max(0, limit))
        selected = entries[offset : offset + limit]
        content = f"Read directory {file_path}, {len(selected)} entries\n" + "\n".join(selected)
        if offset + len(selected) < len(entries):
            content += (
                f"\n[Output truncated. Continue reading with offset: {offset + len(selected)}]"
            )
        return ToolMessage(
            content=content,
            name="read_file",
            tool_call_id=runtime.tool_call_id,
            additional_kwargs={"read_file_path": file_path},
        )

    def _read_media(
        self, file_path: str, native_media: bool, runtime: ToolRuntime[Any]
    ) -> ToolMessage | None:
        """检测并直接读取图片或 PDF 二进制媒体数据为 Base64 消息内容块。

        Args:
            file_path (str): 文件路径。
            native_media (bool): 是否将 PDF 作为媒体二进制直传。
            runtime (ToolRuntime[Any]): 工具运行时对象。

        Returns:
            ToolMessage | None: 包含媒体内容块的工具消息；若非支持的媒体文件则返回 None。
        """
        try:
            value = FileReader.read_supported_media(
                self.backend.resolve_path(file_path), pdf=native_media
            )
        except Exception as exc:
            return ToolMessage(
                content=f"{type(exc).__name__}: {exc}",
                status="error",
                name="read_file",
                tool_call_id=runtime.tool_call_id,
            )
        if value is None:
            return None
        mime, content = value
        return ToolMessage(
            content_blocks=[
                create_file_block(base64=content, mime_type=mime)
                if mime == "application/pdf"
                else create_image_block(base64=content, mime_type=mime)
            ],
            name="read_file",
            tool_call_id=runtime.tool_call_id,
            additional_kwargs={"read_file_path": file_path, "read_file_media_type": mime},
        )

    def _read_file(
        self,
        file_path: str,
        runtime: ToolRuntime[Any],
        offset: int = 0,
        limit: int = 2000,
        native_media: bool = False,
    ) -> ToolMessage:
        """执行同步文件/目录/多模态读取操作。

        Args:
            file_path (str): 目标文件或目录路径。
            runtime (ToolRuntime[Any]): 运行时注入上下文。
            offset (int): 起始行偏移量。
            limit (int): 最大读取行数。
            native_media (bool): 是否按二进制媒体直传 PDF。

        Returns:
            ToolMessage: 读取结果消息。
        """
        if self._skills_read is not None and file_path.startswith("/.agent-harness/skills/"):
            return self._skills_read(
                file_path.removeprefix("/.agent-harness/skills"), runtime, offset, limit
            )
        if limit <= 0:
            return self._original_read(file_path, runtime, offset, limit)
        file_path, error = self._read_path(file_path)
        if error:
            return ToolMessage(
                content=error, status="error", name="read_file", tool_call_id=runtime.tool_call_id
            )
        if self.backend.resolve_path(file_path).is_dir():
            return self._read_directory(file_path, offset, limit, runtime)
        if (result := self._read_media(file_path, native_media, runtime)) is not None:
            return result
        message = self._original_read(file_path, runtime, offset, limit)
        if message.status == "success":
            message.additional_kwargs["read_file_path"] = file_path
        return message

    async def _aread_file(
        self,
        file_path: str,
        runtime: ToolRuntime[Any],
        offset: int = 0,
        limit: int = 2000,
        native_media: bool = False,
    ) -> ToolMessage:
        """执行异步非阻塞文件/目录/多模态读取操作。

        Args:
            file_path (str): 目标路径。
            runtime (ToolRuntime[Any]): 运行时上下文。
            offset (int): 行偏移量。
            limit (int): 最大行数。
            native_media (bool): 是否将 PDF 作为媒体流直传。

        Returns:
            ToolMessage: 异步读取结果消息。
        """
        if self._skills_aread is not None and file_path.startswith("/.agent-harness/skills/"):
            return await self._skills_aread(
                file_path.removeprefix("/.agent-harness/skills"), runtime, offset, limit
            )
        if limit <= 0:
            return await self._original_aread(file_path, runtime, offset, limit)
        file_path, error = await asyncio.to_thread(self._read_path, file_path)
        if error:
            return ToolMessage(
                content=error, status="error", name="read_file", tool_call_id=runtime.tool_call_id
            )
        if self.backend.resolve_path(file_path).is_dir():
            return await asyncio.to_thread(self._read_directory, file_path, offset, limit, runtime)
        if (
            result := await asyncio.to_thread(self._read_media, file_path, native_media, runtime)
        ) is not None:
            return result
        message = await self._original_aread(file_path, runtime, offset, limit)
        if message.status == "success":
            message.additional_kwargs["read_file_path"] = file_path
        return message

    def _create_read_file_tool(self) -> StructuredTool:
        """重写父类构建读文件工具方法，挂载增强型 NativeReadSchema。

        Returns:
            StructuredTool: 构造的 read_file 工具。
        """
        original = super()._create_read_file_tool()
        assert (
            isinstance(original, StructuredTool)
            and original.func is not None
            and original.coroutine is not None
        )
        self._original_read: Callable[..., ToolMessage] = original.func
        self._original_aread: Callable[..., Awaitable[ToolMessage]] = original.coroutine
        return StructuredTool.from_function(
            name="read_file",
            description=original.description
            + " Directories are paged with the same zero-based offset. Unique Unicode filename variants are recovered and authorized.",
            func=self._read_file,
            coroutine=self._aread_file,
            args_schema=NativeReadSchema,
        )

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """在模型前置钩子中动态探查已读文件所在目录树中的 AGENTS.md 规范文件并注入提示词。

        Args:
            state (Any): 状态字典。
            runtime (Any): 运行时上下文。

        Returns:
            dict[str, Any] | None: 包含注入规范消息的更新字典；若无新指令则返回 None。
        """
        messages = state["messages"]
        loaded = {
            path
            for message in messages
            if isinstance(message, HumanMessage)
            for path in (
                message.additional_kwargs.get("harness_instructions", [])
                or message.additional_kwargs.get("opencode_instructions", [])
            )
        }
        candidates: dict[str, str] = {}
        for message in reversed(messages):
            if isinstance(message, AIMessage):
                break
            if (
                not isinstance(message, ToolMessage)
                or message.name != "read_file"
                or message.status != "success"
            ):
                continue
            file_path = message.additional_kwargs.get("read_file_path")
            if file_path is None:
                continue
            try:
                resolved = self.backend.resolve_path(file_path)
                parent = resolved if resolved.is_dir() else resolved.parent
                while parent != self.backend.cwd and parent.is_relative_to(self.backend.cwd):
                    instruction = (parent / "AGENTS.md").resolve()
                    virtual = self.backend.virtual_path(instruction)
                    if (
                        instruction != resolved
                        and str(instruction) not in loaded
                        and instruction.is_file()
                        and not self._authorize("read", virtual)
                    ):
                        candidates[str(instruction)] = instruction.read_text(encoding="utf-8-sig")
                    parent = parent.parent
            except Exception:
                continue
        if not candidates:
            return None
        return {
            "messages": [
                HumanMessage(
                    content="\n\n".join(
                        f"Instructions from: {path}\n{content}"
                        for path, content in candidates.items()
                    ),
                    additional_kwargs={
                        "harness_instructions": list(candidates),
                        "opencode_instructions": list(candidates),
                    },
                )
            ]
        }

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """以异步线程方式执行前置模型规范指令注入。

        Args:
            state (Any): 状态字典。
            runtime (Any): 运行时上下文。

        Returns:
            dict[str, Any] | None: 状态更新字典或 None。
        """
        return await asyncio.to_thread(self.before_model, state, runtime)

    def _grep_result(
        self,
        pattern: str,
        output_mode: Literal["files_with_matches", "content", "count"],
        value: GrepResult,
        runtime: ToolRuntime[Any],
    ) -> ToolMessage:
        """过滤无权限的 Grep 匹配条目，组装包含结构化 artifact 的 ToolMessage。

        Args:
            pattern (str): 检索模式。
            output_mode (Literal["files_with_matches", "content", "count"]): 输出格式模式。
            value (GrepResult): 原始检索结果对象。
            runtime (ToolRuntime[Any]): 工具运行时。

        Returns:
            ToolMessage: 格式化过滤后的工具消息。
        """
        matches = value.matches or []
        filtered = _filter_grep_matches_by_permission(
            self._permissions, matches, operation="read", backend=self.backend
        )
        content, status = _format_grep_tool_result(
            GrepResult(error=value.error, matches=filtered, truncated=value.truncated),
            output_mode,
            pattern,
            backend_had_matches=bool(matches),
        )
        allowed = {match["path"] for match in filtered}
        artifact = None
        if isinstance(value, StructuredGrepResult):
            artifact = {
                "matches": [
                    match for match in value.source_matches if match["entry"]["path"] in allowed
                ],
                "truncated": value.truncated,
            }
        return ToolMessage(
            content=content,
            status=status,
            name="grep",
            tool_call_id=runtime.tool_call_id,
            artifact=artifact,
        )

    def _grep(
        self,
        pattern: str,
        runtime: ToolRuntime[Any],
        path: str | None = None,
        glob: str | None = None,
        output_mode: Literal["files_with_matches", "content", "count"] = "files_with_matches",
        max_count: int | None = None,
        literal: bool = True,
        case_sensitive: bool = True,
    ) -> ToolMessage:
        """执行同步 grep 内容检索。

        Args:
            pattern (str): 检索词。
            runtime (ToolRuntime[Any]): 运行时。
            path (str | None): 相对路径。
            glob (str | None): 文件名过滤通配符。
            output_mode (Literal["files_with_matches", "content", "count"]): 展现模式。
            max_count (int | None): 最大返回条目。
            literal (bool): 是否字面量精确检索。
            case_sensitive (bool): 是否大小写敏感。

        Returns:
            ToolMessage: 结果消息。
        """
        error = self._authorize("read", path or "/")
        value = (
            GrepResult(error=error)
            if error
            else self.backend.search.grep(
                pattern,
                path,
                glob,
                max_count=max_count or self._grep_max_count or 100,
                literal=literal,
                case_sensitive=case_sensitive,
            )
        )
        return self._grep_result(pattern, output_mode, value, runtime)

    async def _agrep(
        self,
        pattern: str,
        runtime: ToolRuntime[Any],
        path: str | None = None,
        glob: str | None = None,
        output_mode: Literal["files_with_matches", "content", "count"] = "files_with_matches",
        max_count: int | None = None,
        literal: bool = True,
        case_sensitive: bool = True,
    ) -> ToolMessage:
        """执行异步非阻塞 grep 内容检索。

        Args:
            pattern (str): 关键词。
            runtime (ToolRuntime[Any]): 运行时。
            path (str | None): 相对路径。
            glob (str | None): 文件过滤通配符。
            output_mode (Literal["files_with_matches", "content", "count"]): 模式。
            max_count (int | None): 最大数量。
            literal (bool): 是否字面精确匹配。
            case_sensitive (bool): 是否区分大小写。

        Returns:
            ToolMessage: 异步检索结果。
        """
        error = self._authorize("read", path or "/")
        value = (
            GrepResult(error=error)
            if error
            else await self.backend.search.agrep(
                pattern,
                path,
                glob,
                max_count=max_count or self._grep_max_count or 100,
                literal=literal,
                case_sensitive=case_sensitive,
            )
        )
        return self._grep_result(pattern, output_mode, value, runtime)

    def _create_grep_tool(self) -> StructuredTool:
        """重写父类构建 grep 检索工具方法，挂载 SearchSchema。

        Returns:
            StructuredTool: 构造好的 grep 工具。
        """
        return StructuredTool.from_function(
            name="grep",
            description="Find literal text or ripgrep regular expressions. Use literal=false for regex; narrow with path/glob. Results are bounded and permission filtered.",
            func=self._grep,
            coroutine=self._agrep,
            args_schema=SearchSchema,
        )

    def _glob_result(self, value: GlobResult, runtime: ToolRuntime[Any]) -> ToolMessage:
        """应用读权限过滤 glob 匹配路径并返回 ToolMessage。

        Args:
            value (GlobResult): Glob 查询结果对象。
            runtime (ToolRuntime[Any]): 运行时上下文。

        Returns:
            ToolMessage: 工具执行结果消息。
        """
        paths = _apply_permissions_to_glob_results(
            self._permissions, value.matches or [], backend=self.backend
        )
        content = value.error or _format_glob_tool_result(
            paths, truncated=value.truncated, truncation_reason=value.truncation_reason
        )
        return ToolMessage(
            content=content,
            status="error" if value.error else "success",
            name="glob",
            tool_call_id=runtime.tool_call_id,
        )

    def _glob(
        self,
        pattern: str,
        runtime: ToolRuntime[Any],
        path: str | None = None,
        hidden: bool = False,
        limit: int = 100,
    ) -> ToolMessage:
        """执行同步 glob 通配文件查找。

        Args:
            pattern (str): 通配表达式。
            runtime (ToolRuntime[Any]): 运行时。
            path (str | None): 可选相对路径。
            hidden (bool): 是否搜寻隐藏文件。
            limit (int): 最大数量上限。

        Returns:
            ToolMessage: 结果消息。
        """
        error = self._authorize("read", path or "/")
        value = (
            GlobResult(error=error)
            if error
            else self.backend.search.glob(pattern, path, hidden=hidden, limit=limit)
        )
        return self._glob_result(value, runtime)

    async def _aglob(
        self,
        pattern: str,
        runtime: ToolRuntime[Any],
        path: str | None = None,
        hidden: bool = False,
        limit: int = 100,
    ) -> ToolMessage:
        """执行异步非阻塞 glob 通配文件查找。

        Args:
            pattern (str): 通配符。
            runtime (ToolRuntime[Any]): 运行时。
            path (str | None): 相对路径。
            hidden (bool): 是否包含隐藏文件。
            limit (int): 上限数量。

        Returns:
            ToolMessage: 异步结果消息。
        """
        error = self._authorize("read", path or "/")
        value = (
            GlobResult(error=error)
            if error
            else await self.backend.search.aglob(pattern, path, hidden=hidden, limit=limit)
        )
        return self._glob_result(value, runtime)

    def _create_glob_tool(self) -> StructuredTool:
        """重写父类构建 glob 工具方法，挂载 FileGlobSchema。

        Returns:
            StructuredTool: 构造的 glob 工具。
        """
        return StructuredTool.from_function(
            name="glob",
            description="Find files with ripgrep glob and ignore rules. Hidden files are optional; .git directories are excluded. Results are bounded and permission filtered.",
            func=self._glob,
            coroutine=self._aglob,
            args_schema=FileGlobSchema,
        )

    def _apply_patch(self, patch_text: str, runtime: ToolRuntime[Any]) -> ToolMessage:
        """执行同步多文件补丁应用。

        Args:
            patch_text (str): 补丁文本。
            runtime (ToolRuntime[Any]): 运行时对象。

        Returns:
            ToolMessage: 包含应用摘要与结构化差异 artifact 的工具消息。
        """
        try:
            content, artifact = self.backend.apply_patch(
                patch_text, lambda path: self._authorize("write", path)
            )
        except Exception as exc:
            return ToolMessage(
                content=f"{type(exc).__name__}: {exc}",
                status="error",
                name="apply_patch",
                tool_call_id=runtime.tool_call_id,
            )
        return ToolMessage(
            content=content,
            artifact=artifact,
            name="apply_patch",
            tool_call_id=runtime.tool_call_id,
        )

    async def _aapply_patch(self, patch_text: str, runtime: ToolRuntime[Any]) -> ToolMessage:
        """执行异步非阻塞多文件补丁应用。

        Args:
            patch_text (str): 补丁文本。
            runtime (ToolRuntime[Any]): 运行时。

        Returns:
            ToolMessage: 异步补丁结果消息。
        """
        try:
            content, artifact = await self.backend.aapply_patch(
                patch_text, lambda path: self._authorize("write", path)
            )
        except Exception as exc:
            return ToolMessage(
                content=f"{type(exc).__name__}: {exc}",
                status="error",
                name="apply_patch",
                tool_call_id=runtime.tool_call_id,
            )
        return ToolMessage(
            content=content,
            artifact=artifact,
            name="apply_patch",
            tool_call_id=runtime.tool_call_id,
        )

    def _create_patch_tool(self) -> StructuredTool:
        """构建原生多文件补丁应用工具 apply_patch。

        Returns:
            StructuredTool: 构造好的 apply_patch 工具实例。
        """
        return StructuredTool.from_function(
            name="apply_patch",
            description="Apply a multi-file patch. Format: *** Begin Patch, then *** Add File: path with +lines, *** Delete File: path, or *** Update File: path with @@ hunks and optional *** Move to: path; finish with *** End Patch. All paths and hunks are checked before writing. Updates may use *** End of File. Earlier operations may remain if a later filesystem write fails.",
            func=self._apply_patch,
            coroutine=self._aapply_patch,
            args_schema=PatchSchema,
        )
