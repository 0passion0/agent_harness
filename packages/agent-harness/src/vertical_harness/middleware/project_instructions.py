"""Workspace instructions loaded into each model request."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deepagents.middleware.filesystem import FilesystemPermission, _permission_error
from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import HumanMessage, SystemMessage

from vertical_harness.storage.document_backend import DocumentFilesystemBackend


@dataclass(eq=False)
class ProjectInstructionsMiddleware(AgentMiddleware):
    backend: DocumentFilesystemBackend
    permissions: list[FilesystemPermission] = field(default_factory=list)

    def _read(self, file_path: str, *, encoding: str = "utf-8") -> tuple[Path, str] | None:
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
        messages = []
        changed = False
        for message in request.messages:
            if not isinstance(message, HumanMessage) or not (
                paths := message.additional_kwargs.get("opencode_instructions")
            ):
                messages.append(message)
                continue
            files = {}
            for source in paths:
                path = Path(source)
                # Checkpointed guidance must not gain access after a symlink or workspace changes.
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
            if files:
                messages.append(
                    message.model_copy(
                        update={
                            "content": content,
                            "additional_kwargs": {
                                **message.additional_kwargs,
                                "opencode_instructions": list(files),
                            },
                        }
                    )
                )
        return request.override(messages=messages) if changed else request

    def _prepare(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        request = self._refresh_messages(request)
        loaded = self._read("/AGENTS.md")
        if loaded is None:
            return request
        path, content = loaded
        instructions = {"type": "text", "text": f"Instructions from: {path}\n{content}"}
        system_message = request.system_message
        if system_message is None:
            system_message = SystemMessage(content=[instructions])
        else:
            system_message = system_message.model_copy(
                update={"content": [*system_message.content_blocks, instructions]}
            )
        return request.override(system_message=system_message)

    def wrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        return handler(self._prepare(request))

    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        return await handler(await asyncio.to_thread(self._prepare, request))
