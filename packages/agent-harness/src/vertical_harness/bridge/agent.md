# 宿主协议桥接

StdioServer 负责标准输入输出 JSON-RPC；DesktopRuntime 校验桌面配置并转换为 HarnessSettings；DesktopMessages 和 AttachmentContent 组装附件消息。

- stdout 只承载当前 JSON-RPC 协议，日志写 stderr；审批使用原生 tool_approval 和单决策对象，不保留旧载荷适配。
- 用户路径和附件在入口校验，不把原始请求字典传入底层服务。
- 协议断开、EOF 和 shutdown 必须等待应用清理。
- 文档文本提取属于 filesystem/DocumentText，不放在桥接层。
