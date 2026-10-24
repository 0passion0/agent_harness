"""工作区文件系统操作领域包，包含文档提取、原子文本读写、Ripgrep 检索与多文件补丁引擎。"""

from vertical_harness.filesystem.document_backend import DocumentFilesystemBackend
from vertical_harness.filesystem.resources import HarnessResources

__all__ = ["DocumentFilesystemBackend", "HarnessResources"]
