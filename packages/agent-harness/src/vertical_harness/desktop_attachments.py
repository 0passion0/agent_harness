from __future__ import annotations

import base64
from pathlib import Path
from typing import Any


def extract_text(path: Path) -> str:
    """Extract local documents as Markdown without extension plugins or cloud services."""
    from markitdown import MarkItDown

    return MarkItDown(enable_plugins=False).convert_local(str(path)).text_content or "无可提取文字"


def content_blocks(
    sources: list[tuple[dict[str, str], Path]], text: str, workspace_root: Path
) -> str | list[dict[str, Any]]:
    if not sources:
        return text
    blocks: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for ref, path in sources:
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            blocks.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{ref['media_type']};base64,{data}"},
                }
            )
            continue
        path_in_workspace = f"/{path.relative_to(workspace_root).as_posix()}"
        blocks.append(
            {
                "type": "text",
                "text": (
                    f"Attachment: {ref['name']}\nPath: {path_in_workspace}\n"
                    + (
                        "Write a local table-summary .py script and run it with execute_python_file; do not print the full table. "
                        "Otherwise start with read_file(offset=0, limit=40)."
                        if path.suffix.lower() in {".csv", ".parquet", ".xlsx"}
                        else "Start with read_file(offset=0, limit=40); if more is needed, continue with later offsets."
                    )
                ),
            }
        )
    return blocks
