# 桌面桥接模块 (vertical_harness/bridge) - Agent 指南与规范

## 1. 目录定位与职责
`vertical_harness/bridge/` 负责宿主智能体与桌面客户端（Desktop/Frontend）之间的协议桥接与数据交换。
提供 JSON-RPC 消息结构封装、多模态附件内容解析及宿主运行时环境检测等基础设施。

## 2. 核心类与文件
- `__init__.py`：统一导出公共类 `AttachmentContent`、`DesktopMessages`、`DesktopRuntime`。
- `attachments.py` (`AttachmentContent`)：
  - 多模态附件统一解析与处理，支持本地文件、图片、剪贴板数据及 Base64 编码载荷。
  - 自动识别 MIME 类型并按 LLM 输入标准转换为多模态消息块。
- `messages.py` (`DesktopMessages`)：
  - 封装与桌面端遵循的 JSON-RPC 2.0 协议消息工厂。
  - 标准化构造流式分块输出（chunk）、工具调用状态、人工审批请求与任务生命周期通知。
- `runtime.py` (`DesktopRuntime`)：
  - 宿主运行环境与工作区上下文初始化，提供虚拟环境路径校验与环境变量解析。

## 3. 关键交互与设计约束
1. **协议透明与契约稳定**：
   - 所有向桌面端投递的消息必须严格遵循 `DesktopMessages` 定义的数据结构，禁止直接抛出内部原始不可序列化对象。
2. **零隐私泄漏**：
   - 消息序列化与推送过程中，严禁泄露 API 密钥、系统敏感路径或未过滤的用户敏感内容。
3. **资源管理与生命周期**：
   - 附件处理中产生的临时文件句柄需及时清理，严禁长时间占用系统资源导致文件句柄泄漏。
4. **纯类接口调用**：
   - 外部调用必须直接引用类（如 `DesktopMessages.format_chunk(...)`），严禁使用模块级别的函数别名。
