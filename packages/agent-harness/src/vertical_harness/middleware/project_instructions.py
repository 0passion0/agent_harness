"""工作区全局及子目录 AGENTS.md 规范指令加载与动态刷新中间件模块。

本模块提供 `ProjectInstructionsMiddleware` 中间件，作为智能体项目行为准则与业务上下文的核心供给组件：
1. 双层指令融合：自动优先读取全局系统指令（`~/.agent-harness/AGENTS.md`），再叠加当前工作区根目录指令（`/AGENTS.md`）；
2. 权限受控读取：严格经由安全权限规则过滤器 `_permission_error` 校验，禁止未经授权的文件系统探测；
3. 指令动态响应与历史槽位保护：每次模型推理前动态重新评估之前历史注入的指令文件是否有修改或删除；
   若指令已删除，保留一个占位槽位并标记 `harness_synthetic`，保证 LangGraph 基于索引的检查点和摘要状态不发生错位。
"""

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

    Attributes:
        backend (DocumentFilesystemBackend): 沙箱文件系统后端，用于读取工作区指令。
        permissions (list[FilesystemPermission]): 文件安全访问拦截规则列表。
        system_instructions (Path | None): 系统全局指令文件路径（例如 `~/.agent-harness/AGENTS.md`）。
    """

    backend: DocumentFilesystemBackend
    permissions: list[FilesystemPermission] = field(default_factory=list)
    system_instructions: Path | None = None

    def _read(self, file_path: str, *, encoding: str = "utf-8") -> tuple[Path, str] | None:
        """受控尝试读取指定相对路径文件的文本内容，无权限或不存在时返回 None。

        Args:
            file_path (str): 目标指令文件的相对虚拟路径（如 `"/AGENTS.md"`）。
            encoding (str): 文本解码字符编码，默认 `"utf-8"`。

        Returns:
            tuple[Path, str] | None: 读取成功时返回 `(path, content)` 二元组；若无权读取或文件不存在/为空则返回 None。
        """
        if _permission_error(self.permissions, "read", self.backend, file_path):
            return None
        path = self.backend.resolve_path(file_path)
        try:
            content = path.read_text(encoding=encoding)
        except (FileNotFoundError, PermissionError):
            return None
        if not content:
            return None
        return path, content

    def _refresh_messages(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        """重新校验并刷新请求历史中由之前回合注入的动态指令内容。

        扫描历史消息中带有 `harness_instructions` 的合成消息，重新比对磁盘文件。
        若文件内容发生变动或被删除，则就地刷新消息内容，同时保留消息槽位以防破坏历史检查点。

        Args:
            request (ModelRequest[Any]): 模型调用请求对象。

        Returns:
            ModelRequest[Any]: 刷新消息内容后的新请求实例（若无变动则返回原始请求）。
        """
        messages = []
        changed = False
        for message in request.messages:
            if not isinstance(message, HumanMessage) or not (
                paths := message.additional_kwargs.get("harness_instructions")
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
            kwargs_update: dict[str, object] = {"harness_instructions": list(files)}
            # 原生摘要按检查点消息索引截断，失效指令也必须保留一个不含旧内容的槽位。
            if not files:
                kwargs_update["harness_synthetic"] = "project-instructions"
            messages.append(
                message.model_copy(
                    update={
                        "content": content
                        or "Previously loaded project instructions are no longer available.",
                        "additional_kwargs": {
                            **message.additional_kwargs,
                            **kwargs_update,
                        },
                    }
                )
            )
        return request.override(messages=messages) if changed else request

    def prepare(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        """准备模型请求，将系统全局指令及工作区 AGENTS.md 规范以系统消息内容块形式注入。

        Args:
            request (ModelRequest[Any]): 模型调用请求对象。

        Returns:
            ModelRequest[Any]: 注入了系统级与项目级 AGENTS.md 规范内容块的新请求对象。
        """
        request = self._refresh_messages(request)
        files = []
        if self.system_instructions is not None and self.system_instructions.is_file():
            content = self.system_instructions.read_text(encoding="utf-8")
            if content:
                files.append((self.system_instructions, content))
        loaded = self._read("/AGENTS.md")
        if loaded is not None:
            files.append(loaded)
        if not files:
            return request
        instructions: list[str | dict[str, Any]] = [
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

    async def aprepare(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        """以异步非阻塞方式准备模型请求并注入全局规范指令。

        Args:
            request (ModelRequest[Any]): 模型调用请求。

        Returns:
            ModelRequest[Any]: 准备就绪的请求实例。
        """
        return await asyncio.to_thread(self.prepare, request)

    def wrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """同步包装模型调用，注入项目全局规范指令。

        Args:
            request (ModelRequest[Any]): 模型调用请求。
            handler (Any): 下一级模型调用执行器。

        Returns:
            Any: 模型调用结果。
        """
        return handler(self.prepare(request))

    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """异步包装模型调用，注入项目全局规范指令。

        Args:
            request (ModelRequest[Any]): 异步模型调用请求。
            handler (Any): 下一级异步执行器。

        Returns:
            Any: 异步执行结果。
        """
        return await handler(await self.aprepare(request))
