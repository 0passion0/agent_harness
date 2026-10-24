# 配置与类型

本包保留 CheckpointConfig、枚举和 TraceRedaction 等非数据模型能力。HarnessSettings、LangSmithSettings、FormatterEntry 及其他 Pydantic Schema 全部归 models 包，由其 __init__.py 统一导出。

- 不导入执行器、文件后端或宿主层。
- permission_mode、max_context_tokens 和 agent_content 使用同一套字段与默认值。
- 外部输入在边界校验；凭据用 SecretStr，不打印原始配置。
- CheckpointConfig 返回原生 RunnableConfig，不改变 thread_id 或持久化键。
