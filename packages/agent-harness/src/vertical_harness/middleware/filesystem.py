"""工作区文件系统操作中间件，集成安全分页读取、多模态媒体嗅探、增强通配检索与原子补丁应用。"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from deepagents.backends.protocol import GlobResult, GrepResult
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
from langchain_core.tools import StructuredTool

from vertical_harness.middleware.file_glob_schema import FileGlobSchema
from vertical_harness.middleware.native_read_schema import NativeReadSchema
from vertical_harness.middleware.patch_schema import PatchSchema
from vertical_harness.middleware.search_schema import SearchSchema
from vertical_harness.tools.file_reader import FileReader
from vertical_harness.tools.structured_grep_result import StructuredGrepResult


class HarnessFilesystemMiddleware(FilesystemMiddleware):
    """强化型文件系统操作拦截中间件，提供多文件补丁、Ripgrep 结构化搜索与分页阅读。

    :author: tongliga
    :date: 2026-10-21
    """

    def __init__(self, **kwargs: Any) -> None:
        """初始化 HarnessFilesystemMiddleware，附加原子多文件补丁工具。

        :param kwargs: 透传给父类 FilesystemMiddleware 的参数字典。
        :type kwargs: typing.Any
        :author: tongliga
        :date: 2026-10-21
        """
        super().__init__(**kwargs)
        self.tools.append(self._create_patch_tool())

    def _authorize(self, operation: Literal["read", "write"], path: str) -> str | None:
        """校验当前路径与操作类型是否在工作区权限许可范围内。

        :param operation: 操作类型（'read' 或 'write'）。
        :type operation: typing.Literal["read", "write"]
        :param path: 虚拟文件路径。
        :type path: str
        :return: 错误描述信息，若授权通过则返回 None。
        :rtype: str | None
        :author: tongliga
        :date: 2026-10-21
        """
        try:
            return _permission_error(
                self._permissions, operation, self.backend, validate_path(path)
            )
        except ValueError as exc:
            return str(exc)

    def _read_path(self, file_path: str) -> tuple[str, str | None]:
        """安全解析并校验读路径，支持 Unicode 规范化名称自动容错恢复。

        :param file_path: 目标虚拟路径。
        :type file_path: str
        :return: (最终虚拟路径, 权限或不存在错误信息) 元组。
        :rtype: tuple[str, str | None]
        :author: tongliga
        :date: 2026-10-21
        """
        if error := self._authorize("read", file_path):
            return file_path, error
        validated = validate_path(file_path)
        try:
            path = self.backend._resolve_path(validated)
            if path.exists():
                return validated, None
            alternate = FileReader.recover_read_path(self.backend.cwd, path)
            if alternate is None:
                return validated, FileReader.missing_read_message(
                    validated, path, lambda candidate: self._authorize("read", candidate)
                )
            recovered = self.backend._to_virtual_path(alternate)
            return recovered, self._authorize("read", recovered)
        except (OSError, ValueError) as exc:
            return validated, f"Cannot read file ({type(exc).__name__})."

    def _read_directory(
        self, file_path: str, offset: int, limit: int, runtime: ToolRuntime
    ) -> ToolMessage:
        """安全分页读取目录下的直接子文件与子目录列表。

        :param file_path: 虚拟目录路径。
        :type file_path: str
        :param offset: 分页起始偏移。
        :type offset: int
        :param limit: 分页读取数量上限。
        :type limit: int
        :param runtime: 工具运行时。
        :type runtime: langchain.tools.ToolRuntime
        :return: 封装好的 ToolMessage 消息。
        :rtype: langchain_core.messages.ToolMessage
        :author: tongliga
        :date: 2026-10-21
        """
        try:
            path = self.backend._resolve_path(file_path)
            entries = [
                name + ("/" if is_dir else "")
                for name, is_dir in FileReader.directory_entries(path)
                if not self._authorize("read", file_path.rstrip("/") + "/" + name)
            ]
        except (OSError, ValueError) as exc:
            return ToolMessage(
                content=f"Cannot read directory: {exc}",
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
        self, file_path: str, native_media: bool, runtime: ToolRuntime
    ) -> ToolMessage | None:
        """检测并直接读取图片或 PDF 二进制媒体数据为 Base64 消息内容块。

        :param file_path: 虚拟文件路径。
        :type file_path: str
        :param native_media: 是否启用 PDF 原生媒体投递。
        :type native_media: bool
        :param runtime: 工具运行时。
        :type runtime: langchain.tools.ToolRuntime
        :return: 包含多模态块的 ToolMessage 或 None（非媒体文件）。
        :rtype: langchain_core.messages.ToolMessage | None
        :author: tongliga
        :date: 2026-10-21
        """
        try:
            value = FileReader.read_supported_media(
                self.backend._resolve_path(file_path), pdf=native_media
            )
        except (OSError, ValueError) as exc:
            return ToolMessage(
                content=str(exc),
                status="error",
                name="read_file",
                tool_call_id=runtime.tool_call_id,
            )
        if value is None:
            return None
        mime, content = value
        return ToolMessage(
            content_blocks=[
                {
                    "type": "file" if mime == "application/pdf" else "image",
                    "base64": content,
                    "mime_type": mime,
                }
            ],
            name="read_file",
            tool_call_id=runtime.tool_call_id,
            additional_kwargs={"read_file_path": file_path, "read_file_media_type": mime},
        )

    def _read_file(
        self,
        file_path: str,
        runtime: ToolRuntime,
        offset: int = 0,
        limit: int = 2000,
        native_media: bool = False,
    ) -> ToolMessage:
        """执行同步文件/目录/多模态读取操作。

        :param file_path: 文件路径。
        :type file_path: str
        :param runtime: 工具运行时。
        :type runtime: langchain.tools.ToolRuntime
        :param offset: 起始行偏移。
        :type offset: int
        :param limit: 读取最大行数。
        :type limit: int
        :param native_media: 是否原生读取多模态媒体。
        :type native_media: bool
        :return: 读取结果 ToolMessage。
        :rtype: langchain_core.messages.ToolMessage
        :author: tongliga
        :date: 2026-10-21
        """
        if limit <= 0:
            return self._original_read_tool.func(file_path, runtime, offset, limit)
        file_path, error = self._read_path(file_path)
        if error:
            return ToolMessage(
                content=error, status="error", name="read_file", tool_call_id=runtime.tool_call_id
            )
        if self.backend._resolve_path(file_path).is_dir():
            return self._read_directory(file_path, offset, limit, runtime)
        if (result := self._read_media(file_path, native_media, runtime)) is not None:
            return result
        message = self._original_read_tool.func(file_path, runtime, offset, limit)
        if message.status == "success":
            message.additional_kwargs["read_file_path"] = file_path
        return message

    async def _aread_file(
        self,
        file_path: str,
        runtime: ToolRuntime,
        offset: int = 0,
        limit: int = 2000,
        native_media: bool = False,
    ) -> ToolMessage:
        """执行异步非阻塞文件/目录/多模态读取操作。

        :param file_path: 文件路径。
        :type file_path: str
        :param runtime: 工具运行时。
        :type runtime: langchain.tools.ToolRuntime
        :param offset: 起始行偏移。
        :type offset: int
        :param limit: 读取最大行数。
        :type limit: int
        :param native_media: 是否原生多模态读取。
        :type native_media: bool
        :return: ToolMessage。
        :rtype: langchain_core.messages.ToolMessage
        :author: tongliga
        :date: 2026-10-21
        """
        if limit <= 0:
            return await self._original_read_tool.coroutine(file_path, runtime, offset, limit)
        file_path, error = await asyncio.to_thread(self._read_path, file_path)
        if error:
            return ToolMessage(
                content=error, status="error", name="read_file", tool_call_id=runtime.tool_call_id
            )
        if self.backend._resolve_path(file_path).is_dir():
            return await asyncio.to_thread(self._read_directory, file_path, offset, limit, runtime)
        if (
            result := await asyncio.to_thread(self._read_media, file_path, native_media, runtime)
        ) is not None:
            return result
        message = await self._original_read_tool.coroutine(file_path, runtime, offset, limit)
        if message.status == "success":
            message.additional_kwargs["read_file_path"] = file_path
        return message

    def _create_read_file_tool(self) -> StructuredTool:
        """重写父类构建读文件工具方法，挂载增强型 NativeReadSchema。

        :return: 增强的 read_file 工具。
        :rtype: langchain_core.tools.StructuredTool
        :author: tongliga
        :date: 2026-10-21
        """
        self._original_read_tool = super()._create_read_file_tool()
        return StructuredTool.from_function(
            name="read_file",
            description=self._original_read_tool.description
            + " Directories are paged with the same zero-based offset. Unique Unicode filename variants are recovered and authorized.",
            func=self._read_file,
            coroutine=self._aread_file,
            args_schema=NativeReadSchema,
        )

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """在模型前置钩子中动态探查已读文件所在目录树中的 AGENTS.md 规范文件并注入提示词。

        :param state: 图状态字典。
        :type state: typing.Any
        :param runtime: 中间件运行时。
        :type runtime: typing.Any
        :return: 包含动态指令的消息变更字典或 None。
        :rtype: dict[str, typing.Any] | None
        :author: tongliga
        :date: 2026-10-21
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
                resolved = self.backend._resolve_path(file_path)
                parent = resolved if resolved.is_dir() else resolved.parent
                while parent != self.backend.cwd and parent.is_relative_to(self.backend.cwd):
                    instruction = (parent / "AGENTS.md").resolve()
                    virtual = self.backend._to_virtual_path(instruction)
                    if (
                        instruction != resolved
                        and str(instruction) not in loaded
                        and instruction.is_file()
                        and not self._authorize("read", virtual)
                    ):
                        candidates[str(instruction)] = instruction.read_text(encoding="utf-8-sig")
                    parent = parent.parent
            except (OSError, ValueError):
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

        :param state: 图状态。
        :type state: typing.Any
        :param runtime: 运行时。
        :type runtime: typing.Any
        :return: 变更字典或 None。
        :rtype: dict[str, typing.Any] | None
        :author: tongliga
        :date: 2026-10-21
        """
        return await asyncio.to_thread(self.before_model, state, runtime)

    def _grep_result(
        self, pattern: str, output_mode: str, value: GrepResult, runtime: ToolRuntime
    ) -> ToolMessage:
        """过滤无权限的 Grep 匹配条目，组装包含结构化 artifact 的 ToolMessage。

        :param pattern: 检索模式。
        :type pattern: str
        :param output_mode: 输出展示模式。
        :type output_mode: str
        :param value: 原始搜索结果模型。
        :type value: deepagents.backends.protocol.GrepResult
        :param runtime: 工具运行时。
        :type runtime: langchain.tools.ToolRuntime
        :return: 封装后的 ToolMessage。
        :rtype: langchain_core.messages.ToolMessage
        :author: tongliga
        :date: 2026-10-21
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
        runtime: ToolRuntime,
        path: str | None = None,
        glob: str | None = None,
        output_mode: str = "files_with_matches",
        max_count: int | None = None,
        literal: bool = True,
        case_sensitive: bool = True,
    ) -> ToolMessage:
        """执行同步 grep 内容检索。

        :param pattern: 搜索文本模式。
        :type pattern: str
        :param runtime: 工具运行时。
        :type runtime: langchain.tools.ToolRuntime
        :param path: 起始路径。
        :type path: str | None
        :param glob: 文件名过滤模式。
        :type glob: str | None
        :param output_mode: 输出格式模式。
        :type output_mode: str
        :param max_count: 最大匹配数。
        :type max_count: int | None
        :param literal: 是否为字面量搜索。
        :type literal: bool
        :param case_sensitive: 是否区分大小写。
        :type case_sensitive: bool
        :return: ToolMessage。
        :rtype: langchain_core.messages.ToolMessage
        :author: tongliga
        :date: 2026-10-21
        """
        error = self._authorize("read", path or "/")
        value = (
            GrepResult(error=error)
            if error
            else self.backend.search.grep(
                pattern,
                path,
                glob,
                max_count=max_count or self._grep_max_count,
                literal=literal,
                case_sensitive=case_sensitive,
            )
        )
        return self._grep_result(pattern, output_mode, value, runtime)

    async def _agrep(
        self,
        pattern: str,
        runtime: ToolRuntime,
        path: str | None = None,
        glob: str | None = None,
        output_mode: str = "files_with_matches",
        max_count: int | None = None,
        literal: bool = True,
        case_sensitive: bool = True,
    ) -> ToolMessage:
        """执行异步非阻塞 grep 内容检索。

        :param pattern: 搜索文本模式。
        :type pattern: str
        :param runtime: 工具运行时。
        :type runtime: langchain.tools.ToolRuntime
        :param path: 起始路径。
        :type path: str | None
        :param glob: 文件名过滤模式。
        :type glob: str | None
        :param output_mode: 输出格式模式。
        :type output_mode: str
        :param max_count: 最大匹配数。
        :type max_count: int | None
        :param literal: 是否为字面量搜索。
        :type literal: bool
        :param case_sensitive: 是否区分大小写。
        :type case_sensitive: bool
        :return: ToolMessage。
        :rtype: langchain_core.messages.ToolMessage
        :author: tongliga
        :date: 2026-10-21
        """
        error = self._authorize("read", path or "/")
        value = (
            GrepResult(error=error)
            if error
            else await self.backend.search.agrep(
                pattern,
                path,
                glob,
                max_count=max_count or self._grep_max_count,
                literal=literal,
                case_sensitive=case_sensitive,
            )
        )
        return self._grep_result(pattern, output_mode, value, runtime)

    def _create_grep_tool(self) -> StructuredTool:
        """重写父类构建 grep 检索工具方法，挂载 SearchSchema。

        :return: StructuredTool 工具。
        :rtype: langchain_core.tools.StructuredTool
        :author: tongliga
        :date: 2026-10-21
        """
        return StructuredTool.from_function(
            name="grep",
            description="Find literal text or ripgrep regular expressions. Use literal=false for regex; narrow with path/glob. Results are bounded and permission filtered.",
            func=self._grep,
            coroutine=self._agrep,
            args_schema=SearchSchema,
        )

    def _glob_result(self, value: GlobResult, runtime: ToolRuntime) -> ToolMessage:
        """应用读权限过滤 glob 匹配路径并返回 ToolMessage。

        :param value: 原始 GlobResult 结果。
        :type value: deepagents.backends.protocol.GlobResult
        :param runtime: 工具运行时。
        :type runtime: langchain.tools.ToolRuntime
        :return: ToolMessage。
        :rtype: langchain_core.messages.ToolMessage
        :author: tongliga
        :date: 2026-10-21
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
        runtime: ToolRuntime,
        path: str | None = None,
        hidden: bool = False,
        limit: int = 100,
    ) -> ToolMessage:
        """执行同步 glob 通配文件查找。

        :param pattern: 通配符表达式。
        :type pattern: str
        :param runtime: 工具运行时。
        :type runtime: langchain.tools.ToolRuntime
        :param path: 目录路径。
        :type path: str | None
        :param hidden: 是否包含隐藏文件。
        :type hidden: bool
        :param limit: 最大条目数。
        :type limit: int
        :return: ToolMessage。
        :rtype: langchain_core.messages.ToolMessage
        :author: tongliga
        :date: 2026-10-21
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
        runtime: ToolRuntime,
        path: str | None = None,
        hidden: bool = False,
        limit: int = 100,
    ) -> ToolMessage:
        """执行异步非阻塞 glob 通配文件查找。

        :param pattern: 通配符表达式。
        :type pattern: str
        :param runtime: 工具运行时。
        :type runtime: langchain.tools.ToolRuntime
        :param path: 目录路径。
        :type path: str | None
        :param hidden: 是否包含隐藏文件。
        :type hidden: bool
        :param limit: 最大条目数。
        :type limit: int
        :return: ToolMessage。
        :rtype: langchain_core.messages.ToolMessage
        :author: tongliga
        :date: 2026-10-21
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

        :return: StructuredTool。
        :rtype: langchain_core.tools.StructuredTool
        :author: tongliga
        :date: 2026-10-21
        """
        return StructuredTool.from_function(
            name="glob",
            description="Find files with ripgrep glob and ignore rules. Hidden files are optional; .git directories are excluded. Results are bounded and permission filtered.",
            func=self._glob,
            coroutine=self._aglob,
            args_schema=FileGlobSchema,
        )

    def _apply_patch(self, patch_text: str, runtime: ToolRuntime) -> ToolMessage:
        """执行同步多文件补丁应用。

        :param patch_text: 完整补丁文本。
        :type patch_text: str
        :param runtime: 工具运行时。
        :type runtime: langchain.tools.ToolRuntime
        :return: ToolMessage。
        :rtype: langchain_core.messages.ToolMessage
        :author: tongliga
        :date: 2026-10-21
        """
        try:
            content, artifact = self.backend.apply_patch(
                patch_text, lambda path: self._authorize("write", path)
            )
        except (OSError, ValueError) as exc:
            return ToolMessage(
                content=str(exc),
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

    async def _aapply_patch(self, patch_text: str, runtime: ToolRuntime) -> ToolMessage:
        """执行异步非阻塞多文件补丁应用。

        :param patch_text: 完整补丁文本。
        :type patch_text: str
        :param runtime: 工具运行时。
        :type runtime: langchain.tools.ToolRuntime
        :return: ToolMessage。
        :rtype: langchain_core.messages.ToolMessage
        :author: tongliga
        :date: 2026-10-21
        """
        try:
            content, artifact = await self.backend.aapply_patch(
                patch_text, lambda path: self._authorize("write", path)
            )
        except (OSError, ValueError) as exc:
            return ToolMessage(
                content=str(exc),
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

        :return: StructuredTool。
        :rtype: langchain_core.tools.StructuredTool
        :author: tongliga
        :date: 2026-10-21
        """
        return StructuredTool.from_function(
            name="apply_patch",
            description="Apply a multi-file patch. Format: *** Begin Patch, then *** Add File: path with +lines, *** Delete File: path, or *** Update File: path with @@ hunks and optional *** Move to: path; finish with *** End Patch. All paths and hunks are checked before writing. Updates may use *** End of File. Earlier operations may remain if a later filesystem write fails.",
            func=self._apply_patch,
            coroutine=self._aapply_patch,
            args_schema=PatchSchema,
        )
