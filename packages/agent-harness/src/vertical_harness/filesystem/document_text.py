"""宿主无关的 Office/PDF 文本提取模块。

本模块提供文档解析实用工具 `DocumentText`，利用微软开源的 `markitdown` 工具库，
将 PDF、Word (.docx)、Excel (.xlsx)、PowerPoint (.pptx) 等复杂二进制格式文档
转换为统一的高质量 Markdown 结构化文本，供智能体理解和分析。
"""

from __future__ import annotations

from pathlib import Path


class DocumentText:
    """基于 MarkItDown 的 Office 与 PDF 文档文本提取器。

    静态方法工具类，统一封装复杂文档的文本转录过程，显式关闭外部不受信插件以防范供应链风险。
    """

    @staticmethod
    def extract_text(path: Path) -> str:
        """从指定物理文件路径中提取并转录为 Markdown 或纯文本内容。

        动态导入 `markitdown` 库（按需延迟加载以减少启动开销），禁用第三方插件以保证执行安全。
        若文档为空或仅含不可读图像，则返回默认兜底提示。

        Args:
            path (Path): 待提取内容的物理文件绝对路径（必须是有效存在的支持文件格式）。

        Returns:
            str: 转换生成的结构化 Markdown 文本；若无文字内容则返回 `"无可提取文字"`。
        """
        from markitdown import MarkItDown

        return (
            MarkItDown(enable_plugins=False).convert_local(str(path)).text_content or "无可提取文字"
        )
