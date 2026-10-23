"""工作区全局及子目录 AGENTS.md 规范指令加载与动态刷新中间件模块。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deepagents.middleware.filesystem import FilesystemPermission, _permission_error
from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import HumanMessage, SystemMessage

from vertical_harness.filesystem.document_backend import DocumentFilesystemBackend


@dataclass(eq=False)
class ProjectInstructionsMiddleware(AgentMiddleware):
    """项目级规范指令注入中间件，自动加载根目录与活跃文件作用域的 AGENTS.md。

    :author: tongliga
    :date: 2026-10-21
    """

    backend: DocumentFilesystemBackend
    permissions: list[FilesystemPermission] = field(default_factory=list)
    system_instructions: Path | None = None

    def _read(self, file_path: str, *, encoding: str = "utf-8") -> tuple[Path, str] | None:
        """受控尝试读取指定相对路径文件的文本内容，无权限或不存在时返回 None。

        :param file_path: 工作区相对或虚拟路径。
        :type file_path: str
        :param encoding: 文件读取编码格式（默认 'utf-8'）。
        :type encoding: str
        :return: (物理路径, 文件内容) 元组，读取失败返回 None。
        :rtype: tuple[pathlib.Path, str] | None
        :author: tongliga
        :date: 2026-10-21
        """
        if _permission_error(self.permissions, "read", self.backend, file_path):
            return None
        path = self.backend._resolve_path(file_path)
        try:
            content = path.read_text(encoding=encoding)
        except (FileNotFoundError, PermissionError):
            return None
        if not content:
            return None
        return path, content

    def _refresh_messages(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        """重新校验并刷新请求历史中由之前回合注入的动态指令内容。

        :param request: 原始模型请求对象。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :return: 刷新指令后的模型请求对象。
        :rtype: langchain.agents.middleware.ModelRequest[typing.Any]
        :author: tongliga
        :date: 2026-10-21
        """
        messages = []
        changed = False
        for message in request.messages:
            if not isinstance(message, HumanMessage) or not (
                paths := (
                    message.additional_kwargs.get("harness_instructions")
                    or message.additional_kwargs.get("opencode_instructions")
                )
            ):
                messages.append(message)
                continue
            files = {}
            for source in paths:
                path = Path(source)
                if not path.is_relative_to(self.backend.cwd) or not path.resolve().is_relative_to(
                    self.backend.cwd
                ):
                    continue
                virtual = "/" + path.relative_to(self.backend.cwd).as_posix()
                if loaded := self._read(virtual, encoding="utf-8-sig"):
                    files[str(loaded[0])] = loaded[1]
            content = "\n\n".join(
                f"Instructions from: {path}\n{content}" for path, content in files.items()
            )
            if content == message.content and list(files) == paths:
                messages.append(message)
                continue
            changed = True
            kwargs_update = {}
            if "harness_instructions" in message.additional_kwargs or "opencode_instructions" not in message.additional_kwargs:
                kwargs_update["harness_instructions"] = list(files)
            if "opencode_instructions" in message.additional_kwargs:
                kwargs_update["opencode_instructions"] = list(files)
            # 原生摘要按检查点消息索引截断，失效指令也必须保留一个不含旧内容的槽位。
            if not files:
                kwargs_update["harness_synthetic"] = "project-instructions"
            messages.append(
                message.model_copy(
                    update={
                        "content": content or "Previously loaded project instructions are no longer available.",
                        "additional_kwargs": {
                            **message.additional_kwargs,
                            **kwargs_update,
                        },
                    }
                )
            )
        return request.override(messages=messages) if changed else request

    def _prepare(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        """准备模型请求，将系统全局指令及工作区 AGENTS.md 规范以系统消息内容块形式注入。

        :param request: 原始请求。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :return: 注入系统指令后的请求。
        :rtype: langchain.agents.middleware.ModelRequest[typing.Any]
        :author: tongliga
        :date: 2026-10-22
        """
        # 第一步：刷新请求中的消息队列
        request = self._refresh_messages(request)
        files = []

        # 第二步：依次收集系统全局指令与项目工作区 AGENTS.md 规范内容
        if self.system_instructions is not None and self.system_instructions.is_file():
            content = self.system_instructions.read_text(encoding="utf-8")
            if content:
                files.append((self.system_instructions, content))
        loaded = self._read("/AGENTS.md")
        if loaded is not None:
            files.append(loaded)
        if not files:
            return request

        # 第三步：构建格式化指令块并注入系统消息中
        instructions = [
            {"type": "text", "text": f"Instructions from: {path}\n{content}"}
            for path, content in files
        ]
        system_message = request.system_message
        if system_message is None:
            system_message = SystemMessage(content=instructions)
        else:
            system_message = system_message.model_copy(
                update={"content": [*system_message.content_blocks, *instructions]}
            )
        return request.override(system_message=system_message)

    def wrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """同步包装模型调用，注入项目全局规范指令。

        :param request: 模型请求。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :param handler: 下游调用器。
        :type handler: typing.Any
        :return: 调用响应。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-21
        """
        return handler(self._prepare(request))

    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """异步包装模型调用，注入项目全局规范指令。

        :param request: 模型请求。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :param handler: 异步下游调用器。
        :type handler: typing.Any
        :return: 调用响应。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-21
        """
        return await handler(await asyncio.to_thread(self._prepare, request))
