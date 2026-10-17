"""用于确定性数字引用检查的 Pydantic v2 数据模式定义模块。"""

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class ReferenceCheckInput(BaseModel):
    """数字引用核查工具的输入参数数据模型。"""

    # 严格模式且禁止任何多余未声明参数传入，防止非预期 payload
    model_config = ConfigDict(extra="forbid", strict=True)

    # 包含行内引用的手稿正文纯文本，长度限制 1 到 200,000 字符
    manuscript_text: str = Field(min_length=1, max_length=200_000)
    # 按照正文出现顺序排列的参考文献条目列表，条目数限制 1 到 10,000
    bibliography: list[str] = Field(min_length=1, max_length=10_000)


class ReferenceIssue(BaseModel):
    """单条确定性引用序号错误描述模型。"""

    # 禁止未声明的多余字段
    model_config = ConfigDict(extra="forbid")

    # 错误类型：out_of_range（超出参考文献列表范围）或 uncited（文献列表存在但正文未引用）
    kind: Literal["out_of_range", "uncited"]
    # 对应的引用数字索引，从 1 开始计数
    index: int = Field(ge=1)
    # 针对该错误原因的详细说明
    explanation: str


class ReferenceCheckResult(BaseModel):
    """数字引用核验结果数据模型（不包含未经核实的科学真实性判定）。"""

    # 禁止未声明的多余字段
    model_config = ConfigDict(extra="forbid")

    # 核验状态：passed（完全匹配通过）、issues（存在错误）、not_applicable（手稿中无数字引用）
    status: Literal["passed", "issues", "not_applicable"]
    # 正文中成功提取并去重后的所有有效引用数字序号列表
    cited_indices: list[int]
    # 发现的所有引用异常问题列表
    issues: list[ReferenceIssue]
    # 核验范围界定说明，提示模型本结果仅针对数字索引有效，不涉及事实与语义真实性
    verified_scope: str = "Numeric reference indexes only; not evidence or semantic validity"
