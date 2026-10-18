# 研究引文源码根目录 (packages/research-citation/src) - Agent 指南与规范

## 目录定位与职责
`packages/research-citation/src/` 是 `research-citation` 插件包的源代码根目录，采用标准 `src` 布局。

## 命名空间组织
- `research_citation/`：包含引文审计插件的核心实现：
  - `plugin.py`：插件实现类 `ResearchCitationPlugin`。
  - `workflow.py`：LangGraph 审计状态图定义。
  - `checker/`：底层解析器、评估器与核查工具。
  - `models/`：引文核查领域 Pydantic 数据模型。
  - `skills/`：配套的 Deep Agents 技能指南与静态资源。

## Agent 交互规范
1. **符合宿主插件标准**：本目录下的所有能力导出必须严格符合 `vertical_harness.plugins.contracts` 中定义的协议规范。
2. **纯函数优先**：解析与文本比对逻辑尽量设计为无副作用的纯函数，便于单元测试与并发调用。
3. **日志规范**：所有模块内部使用结构化日志输出审计步骤摘要，杜绝将整篇手稿文本打印至日志中。
