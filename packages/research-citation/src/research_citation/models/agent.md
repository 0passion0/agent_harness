# 引文数据模型 (research_citation/models) - Agent 指南与规范

## 目录定位与职责
`research_citation/models/` 定义了引文核查领域的所有 Pydantic 数据模型与状态 Schema。

## 核心组件与文件
- `check.py`：定义引文标记模型（`CitationToken`）、单条核查详情（`CitationCheckDetail`）等基础结构。
- `audit.py`：定义完整的审计请求模型（`CitationAuditRequest`）、审计结果汇总（`CitationAuditReport`）以及工作流状态机 Schema（`CitationWorkflowState`）。

## 关键约束与安全规范
1. **强类型与字段文档**：每个模型字段必须有明确的类型注解与 `Field(description=...)` 描述。
2. **状态契约兼容**：工作流状态模型需兼容 LangGraph 的 State 序列化与反序列化机制。
3. **输入输出验证**：数据模型兼作 API 与评测边界的验证契约，严格阻断非法格式数据。
