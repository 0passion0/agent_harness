# 研究引文核心命名空间 (research_citation) - Agent 指南与规范

## 目录定位与职责
`research_citation/` 是引文审计插件的顶层业务命名空间。提供完整的引文校验工作流、领域工具及技能资产集成。

## 核心文件与模块
- `__init__.py`：统一公共 API 导出，暴露 `ResearchCitationPlugin`、`scan_numeric` 工具与核心模型。
- `plugin.py`：实现 `DomainPlugin` 接口的 `ResearchCitationPlugin`，负责定义插件清单、声明工作流及工具贡献。
- `workflow.py`：使用 LangGraph 状态图构建的端到端引文审计流水线，包含文本解析、参考文献对照和报告生成节点。
- `checker/`：底层引文提取、匹配验证与 LangChain Tool 封装。
- `models/`：审计输入、中间状态与最终审计报告的模型定义。
- `skills/`：内置的技能说明文件（如 `numeric-citation-audit`）。

## 关键约束与安全规范
1. **脱敏保护**：日志中仅记录引文标号数量、参考文献总数和违规项索引，不可打印手稿全文。
2. **确定性审计**：引文解析与编号匹配结果在相同输入下必须完全确定。
3. **工作流模式适配**：支持独立子图模式与主智能体委派模式（Delegated SubAgent）。
