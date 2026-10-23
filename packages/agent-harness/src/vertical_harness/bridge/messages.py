"""桌面端多模态消息转换与消息序列化模块。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class DesktopMessages:
    """桌面端消息解析与格式化工具类。"""

    @staticmethod
    def attachment_message(
        workspace_root: Path, text: str, attachments: list[dict[str, str]]
    ) -> str | list[str | dict[str, Any]]:
        """校验附件安全性（必须在工作区物理边界内）并生成多模态消息内容块。"""
        root = workspace_root.expanduser().resolve()
        sources = []

        for attachment in attachments:
            if not isinstance(attachment, dict) or set(attachment) != {
                "name",
                "path",
                "media_type",
            }:
                raise ValueError("attachments must contain name, path, and media_type")
            if any(not isinstance(attachment[key], str) for key in attachment):
                raise ValueError("attachment fields must be strings")
            source = Path(attachment["path"])
            if not source.is_absolute():
                raise ValueError("attachment path must be absolute")
            source = source.resolve(strict=True)
            if not source.is_file():
                raise ValueError("attachment path must refer to a file")
            sources.append((attachment, source))

        # 校验全部附件后再读取内容，避免部分读取后才发现越界附件。
        for _, source in sources:
            try:
                source.relative_to(root)
            except ValueError as exc:
                raise ValueError("attachment must be inside the workspace") from exc

        from vertical_harness.bridge.attachments import AttachmentContent

        return AttachmentContent.content_blocks(sources, text, root)

    @staticmethod
    def raw_message(message: Any) -> dict[str, Any]:
        """将 LangChain 消息对象导出为排除空字段的标准 JSON 字典。"""
        return message.model_dump(mode="json", exclude_none=True)
