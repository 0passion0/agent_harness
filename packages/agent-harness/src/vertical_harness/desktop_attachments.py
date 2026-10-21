"""桌面端附件转换与多模态消息内容块构建模块。"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any


def extract_text(path: Path) -> str:
    """在无插件与无外部云服务依赖下，将本地 Office/PDF 文档提取转换为 Markdown 格式文本。

    :param path: 本地文档的物理文件 Path 对象。
    :type path: pathlib.Path
    :return: 提取后的 Markdown 文本字符串；若内容为空则返回 '无可提取文字'。
    :rtype: str
    :raises ModuleNotFoundError: 当未安装可选依赖 markitdown 时抛出。
    :author: tongliga
    :date: 2026-10-21
    """
    # 第一步：局部延迟导入轻量级转换依赖 markitdown
    from markitdown import MarkItDown

    # 第二步：初始化转换器并执行本地离线转换，返回正文内容
    return MarkItDown(enable_plugins=False).convert_local(str(path)).text_content or "无可提取文字"


def content_blocks(
    sources: list[tuple[dict[str, str], Path]], text: str, workspace_root: Path
) -> str | list[dict[str, Any]]:
    """将提示词文本与用户上传的附件列表组装为 LangChain 多模态内容块列表。

    图片文件转换为 Base64 内联图片块；结构化数据（CSV/Parquet/Excel）及文档转换为引导智能体
    使用特定工具分批读取的文本提示块。

    :param sources: 附件元数据字典与工作区物理路径的元组列表。
    :type sources: list[tuple[dict[str, str], pathlib.Path]]
    :param text: 用户输入的原始文本消息。
    :type text: str
    :param workspace_root: 工作区物理根目录路径。
    :type workspace_root: pathlib.Path
    :return: 若无附件则直接返回原文本字符串；有附件时返回包含 text 与 image_url 类型的字典块列表。
    :rtype: str | list[dict[str, typing.Any]]
    :author: tongliga
    :date: 2026-10-21

    .. code-block:: python

        # 示例输出数据结构契约：
        [
            {"type": "text", "text": "分析这份报表"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
            {"type": "text", "text": "Attachment: data.csv\\nPath: /data.csv\\n..."}
        ]
    """
    # 第一步：无附件时直接返回纯文本字符串
    if not sources:
        return text

    # 第二步：初始化基础文本内容块列表
    blocks: list[dict[str, Any]] = [{"type": "text", "text": text}]

    # 第三步：遍历附件源列表逐项转换
    for ref, path in sources:
        # 第四步：若为图片格式，读取二进制并编码为标准 Data URL Base64 块
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            blocks.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{ref['media_type']};base64,{data}"},
                }
            )
            continue

        # 第五步：表格或普通文件转换为提示词引导块，注入工作区虚拟路径
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
