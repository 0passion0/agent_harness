"""桌面端附件转换、文本提取与多模态消息内容块构建模块。"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any


class AttachmentContent:
    """附件内容处理器，支持基于 MarkItDown 的多格式文档提取与多模态数据块封装。

    :author: tongliga
    :date: 2026-10-21
    """

    @staticmethod
    def extract_text(path: Path) -> str:
        """从指定文件路径提取 Markdown 或纯文本内容。

        :param path: 本地文件 Path 对象。
        :type path: pathlib.Path
        :return: 提取出的 Markdown 文本内容，提取失败或为空时返回默认提示。
        :rtype: str
        :author: tongliga
        :date: 2026-10-21
        """
        from markitdown import MarkItDown

        return MarkItDown(enable_plugins=False).convert_local(str(path)).text_content or "无可提取文字"

    @staticmethod
    def content_blocks(
        sources: list[tuple[dict[str, str], Path]], text: str, workspace_root: Path
    ) -> str | list[dict[str, Any]]:
        """将附件列表转换为注入给大模型的多模态或文本指导消息块。

        :param sources: 附件元数据与本地物理路径的元组列表。
        :type sources: list[tuple[dict[str, str], pathlib.Path]]
        :param text: 用户输入的原始提示词。
        :type text: str
        :param workspace_root: 工作区物理根目录。
        :type workspace_root: pathlib.Path
        :return: 若无附件则直接返回纯文本，若有附件则返回结构化消息块列表。
        :rtype: str | list[dict[str, typing.Any]]
        :author: tongliga
        :date: 2026-10-21
        """
        if not sources:
            return text

        blocks: list[dict[str, Any]] = [{"type": "text", "text": text}]

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
