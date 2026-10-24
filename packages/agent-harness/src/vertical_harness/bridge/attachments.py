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
        """将附件列表转换为注入给大模型的多模态或文本指导消息块。

        Args:
            sources: 已通过工作区物理边界与格式校验的附件元组列表，每个元素包含：
                - dict[str, str]: 包含 'name', 'path', 'media_type' 的附件元数据字典。
                - Path: 解析后的物理绝对路径。
            text: 用户输入的文本提示词。
            workspace_root: 工作区物理根目录绝对路径，用于计算文件相对位置。

        Returns:
            若无任何附件，直接返回原始 text 字符串；
            若存在附件，返回组装后的多模态消息块列表，包含纯文本块及图片 image_url 字典或文件引导提示块。
        """
        # 无附件时直接透传纯文本，节省列表包裹开销
        if not sources:
            return text

        blocks: list[str | dict[str, Any]] = [{"type": "text", "text": text}]

        for ref, path in sources:
            suffix = path.suffix.lower()
            # 常见图片格式转换为 base64 image_url 数据块
            if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                data = base64.b64encode(path.read_bytes()).decode("ascii")
                blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{ref['media_type']};base64,{data}"},
                    }
                )
                continue

            # 文档或表格类文件生成基于工作区相对路径的工具调用引导提示
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
