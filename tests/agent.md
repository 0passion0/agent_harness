# 测试目录 (tests) - Agent 指南与规范

## 目录定位与职责
`tests/` 目录包含 Vertical Agent Harness 系统的完整自动化测试套件。涵盖核心引擎组装、模型调用、状态持久化、时间旅行、多租户隔离、流式通信、MCP 工具集成、中间件链以及领域插件端到端链路。

## 测试模块图谱
- `conftest.py`：全局测试夹具（Fixtures），配置模拟模型、测试临时路径与测试环境。
- `test_evaluation.py`：离线与在线评测管线、评分器与 LangSmith 数据集同步测试。
- `test_feedback_and_online.py`：在线用户打分、反馈捕获与评测关联测试。
- `test_logging.py`：日志格式化器（JSON/控制台）、受控字段输出、脱敏保护及调用上下文测试。
- `test_mcp.py`：MCP 协议客户端连接、工具暴露、模式映射与生命周期测试。
- `test_memory.py`：用户记忆检索、定期归纳工作进程与内存状态保存测试。
- `test_model_retry.py`：模型调用异常重试策略、退避与容错机制测试。
- `test_multimodal.py`：多模态输入（图片解析、Base64 编码、类型限制）验证测试。
- `test_observability.py`：LangSmith 与 OpenTelemetry 链路追踪上下文测试。
- `test_schema.py`：配置模型（HarnessSettings）与插件清单结构合法性校验测试。
- `test_stabilization.py`：执行稳定性基准、异常阻断与恢复策略测试。
- `test_store.py`：持久化存储后端（内存、SQLite、Postgres）读写与命名空间隔离测试。
- `test_streaming.py`：Token 级与事件级流式输出流处理测试。
- `test_supervisor_workflows_and_time_travel.py`：监督智能体工作流调度、子智能体委派与 Time Travel（检查点重放/分叉）测试。
- `test_workflow_logging.py`：执行调用树完整性跟踪、操作 ID 映射与私密数据防泄露断言测试。

## 测试编写与执行准则
1. **私密数据防泄露断言**：测试日志捕获时，必须维持 `assert "private" not in output` 的安全不变量。
2. **轻量与隔离**：测试优先使用内存后端（`store_backend="memory"`）和模拟模型（`FakeMessagesListChatModel`），不依赖外部在线网络凭证。
3. **断言精细化**：验证操作时不仅检查返回结果，还须核验操作生命周期事件（如 `_started` 与 `_completed` 严格成对）。
4. **运行命令**：
   - 快速执行全部测试：`uv run pytest`
   - 指定单模块运行：`uv run pytest tests/test_workflow_logging.py`
