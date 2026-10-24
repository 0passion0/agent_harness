"""读取文件后按作用域加载目录指令；状态去重由原生消息 reducer 管理。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from deepagents import FilesystemPermission
from deepagents.backends.utils import validate_path
from deepagents.middleware.filesystem import _permission_error
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from vertical_harness.filesystem.document_backend import DocumentFilesystemBackend


@dataclass(eq=False)
class DirectoryInstructionsMiddleware(AgentMiddleware[Any, Any, Any]):
    """只更新目录指令消息，不注册文件工具或包装模型调用。"""

    backend: DocumentFilesystemBackend
    permissions: list[FilesystemPermission]

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """按已成功读取的文件加载尚未进入状态的目录指令。

        :return: reducer 应用的增量消息；无新指令时返回 None。
        """
        messages = state["messages"]
        loaded = {
            path
            for message in messages
            if isinstance(message, HumanMessage)
            for path in message.additional_kwargs.get("harness_instructions", [])
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
                        and not _permission_error(
                            self.permissions, "read", self.backend, validate_path(virtual)
                        )
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
                    },
                )
            ]
        }

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """异步入口保持相同权限校验和 reducer 语义。"""
        return await asyncio.to_thread(self.before_model, state, runtime)
