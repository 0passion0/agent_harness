"""文件读取（read_file）输入参数验证 Schema 模块。"""

from __future__ import annotations

from deepagents.middleware.filesystem import ReadFileSchema
from pydantic import Field


class NativeReadSchema(ReadFileSchema):
    """文件读取扩展参数模型，支持原生多模态（PDF/图片）直传大模型控制。"""

    native_media: bool = Field(
        default=False,
        description="Send PDFs directly to the model instead of extracting Markdown. Images are detected by signature.",
    )
