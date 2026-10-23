"""桌面附件的多模态消息内容块。"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any


class AttachmentContent:
    """将已校验附件转换为消息块。"""

    @staticmethod
    def content_blocks(
        sources: list[tuple[dict[str, str], Path]], text: str, workspace_root: Path
    ) -> str | list[str | dict[str, Any]]:
        """将附件列表转换为注入给大模型的多模态或文本指导消息块。"""
        if not sources:
            return text

        blocks: list[str | dict[str, Any]] = [{"type": "text", "text": text}]

        for ref, path in sources:
            suffix = path.suffix.lower()
            if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                data = base64.b64encode(path.read_bytes()).decode("ascii")
                blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{ref['media_type']};base64,{data}"},
                    }
                )
                continue

            path_in_workspace = f"/{path.relative_to(workspace_root).as_posix()}"
            instruction = (
                "Write a local table-summary .py script and run it with execute_python_file; do not print the full table. "
                "Otherwise start with read_file(offset=0, limit=40)."
                if suffix in {".csv", ".parquet", ".xlsx"}
                else "Start with read_file(offset=0, limit=40); if more is needed, continue with later offsets."
            )
            blocks.append(
                {
                    "type": "text",
                    "text": f"Attachment: {ref['name']}\nPath: {path_in_workspace}\n{instruction}",
                }
            )
        return blocks
