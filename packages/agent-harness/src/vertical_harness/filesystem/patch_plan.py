"""补丁预检产生的操作顺序、初始快照与预期最终内容。

本模块定义了 `PatchPlan` 实体，作为协同补丁应用器（Patch Editor）进行两阶段提交的核心契约。
在物理写入磁盘之前，先进行全内存干运行（dry-run）计算并生成执行计划，
保存受影响文件的原始内容快照以支持事务回滚或格式化前置验证。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from vertical_harness.filesystem.patch_hunk import PatchHunk


@dataclass
class PatchPlan:
    """复合补丁预检执行计划，记录全量操作顺序、原始快照与预期目标状态。

    Attributes:
        operations (list[tuple[PatchHunk, Path, Path, bytes]]): 待按序提交的底层操作四元组列表：
            - `hunk` (PatchHunk): 关联的补丁块描述对象；
            - `target_path` (Path): 源操作文件的绝对物理路径；
            - `destination_path` (Path): 目标写入/重命名路径（与 target_path 相同表示就地更新）；
            - `data` (bytes): 准备直接写入磁盘的目标文件原始二进制字节流。
        original (dict[Path, str | None]): 各文件在执行任何物理写入前的初始文本快照映射（新文件值为 None）。
        contents (dict[Path, str | None]): 各文件在所有补丁块模拟应用完毕后的预期最终文本映射（删除操作值为 None）。
        names (dict[Path, str]): 物理绝对文件路径到规范化工作区相对路径字符串的映射表。
    """

    # 待按顺序提交的物理操作列表：(hunk, target_path, destination_path, data)
    operations: list[tuple[PatchHunk, Path, Path, bytes]] = field(default_factory=list)
    # 各文件在变更前的原始文本内容快照（文件不存在时为 None）
    original: dict[Path, str | None] = field(default_factory=dict)
    # 各文件在所有 hunk 模拟应用后的预期最终文本内容（删除时为 None）
    contents: dict[Path, str | None] = field(default_factory=dict)
    # 各物理路径对应的规范化虚拟文件名称映射
    names: dict[Path, str] = field(default_factory=dict)
