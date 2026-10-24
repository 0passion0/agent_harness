"""文件读取（read_file）输入参数验证 Schema 模块。

本模块提供 `NativeReadSchema` Pydantic 模型，继承自 DeepAgents 的标准 `ReadFileSchema`。
扩展了多模态原生二进制流传递控制项 `native_media`，允许选择将 PDF 直接作为多模态载荷送入模型
或由本地 MarkItDown 引擎先提取转录为纯文本 Markdown。
"""

from __future__ import annotations

from deepagents.middleware.filesystem import ReadFileSchema
from pydantic import Field


class NativeReadSchema(ReadFileSchema):
    """文件读取扩展参数模型，支持原生多模态（PDF/图片）直传大模型控制。

    Attributes:
        path (str): 目标读取文件在工作区内的相对路径。
        offset (int): 起始读取行偏移量（从 0 开始），默认 0。
        limit (int): 本次分页读取的最大行数配额，默认 2,000。
        native_media (bool): 是否将 PDF 等文件以多模态二进制原样直接交付给模型处理，
            若为 False 则默认提取为 Markdown 文本；图片类文件始终自动嗅探签名交付，默认 False。
    """

    native_media: bool = Field(
        default=False,
        description="Send PDFs directly to the model instead of extracting Markdown. Images are detected by signature.",
    )
