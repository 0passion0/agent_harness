"""桌面端交互桥接与集成领域包，包含附件解析、消息载荷与运行时环境探测。"""

from vertical_harness.bridge.attachments import AttachmentContent
from vertical_harness.bridge.messages import DesktopMessages
from vertical_harness.bridge.runtime import DesktopRuntime

__all__ = [
    "AttachmentContent",
    "DesktopMessages",
    "DesktopRuntime",
]
