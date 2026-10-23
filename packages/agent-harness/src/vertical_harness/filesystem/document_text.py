"""宿主无关的 Office/PDF 文本提取。"""

from pathlib import Path


class DocumentText:
    @staticmethod
    def extract_text(path: Path) -> str:
        """从指定文件路径提取 Markdown 或纯文本内容。"""
        from markitdown import MarkItDown

        return (
            MarkItDown(enable_plugins=False).convert_local(str(path)).text_content or "无可提取文字"
        )
