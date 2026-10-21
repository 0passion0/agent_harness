"""多文件补丁应用（apply_patch）输入参数验证 Schema 模块。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PatchSchema(BaseModel):
    """补丁工具参数模型，接收标准的复合多文件差异更新文本。

    :author: tongliga
    :date: 2026-10-21
    """

    patch_text: str = Field(description="Complete *** Begin Patch / *** End Patch text.")
