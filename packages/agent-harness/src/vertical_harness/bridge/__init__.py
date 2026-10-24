"""桌面端交互桥接与集成领域包。

本领域包负责桌面应用进程与 Agent 运行时之间的数据载荷转换与协议桥接：
1. `AttachmentContent`: 规范桌面端传递的文件/图片附件模型，支持提取纯文本与 Base64 多模态格式；
2. `DesktopMessages`: 校验并组装用于模型输入的 `HumanMessage`，严格防范工作区越界与安全沙箱逃逸；
3. `DesktopRuntime`: 负责运行时环境变量探测与桌面环境特异性配置装配。
"""

from vertical_harness.bridge.attachments import AttachmentContent
from vertical_harness.bridge.messages import DesktopMessages
from vertical_harness.bridge.runtime import DesktopRuntime

__all__ = [
    "AttachmentContent",
    "DesktopMessages",
    "DesktopRuntime",
]
