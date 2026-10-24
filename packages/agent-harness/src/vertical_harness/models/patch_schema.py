"""多文件补丁应用（apply_patch）输入参数验证 Schema 模块。

本模块提供 `PatchSchema` Pydantic 模型，定义 `apply_patch` 工具调用的入参格式，
校验接收完整的标准复合补丁文本。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PatchSchema(BaseModel):
    """补丁工具参数模型，接收标准的复合多文件差异更新文本。

    Attributes:
        patch_text (str): 遵循补丁协议规范的完整文本，必须以 `*** Begin Patch` 开头并以 `*** End Patch` 结尾。
    """

    patch_text: str = Field(description="Complete *** Begin Patch / *** End Patch text.")
