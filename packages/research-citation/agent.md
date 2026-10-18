# 研究引文包目录 (packages/research-citation) - Agent 指南与规范

## 目录定位与职责
`packages/research-citation/` 是 Vertical Agent Harness 的官方示范领域插件包。专注于学术文献、科技论文中的数字引文核查、参考文献对照及合规性审计。

## 目录结构
- `src/`：包源码根目录，包含唯一的领域包命名空间 `research_citation`。
- `pyproject.toml`：依赖声明、包元数据及针对宿主框架 `vertical_harness.plugins` 的 Entry Points 注册声明。

## 核心业务能力
1. **数字标号引文解析**：精确提取论文段落中的 `[1]`、`[2, 3]` 等数字引文锚点。
2. **参考文献表对照**：核验引文数字与末尾参考文献列表（Bibliography）的对应关系，识别断链、缺漏与顺序错乱。
3. **工作流自动化审计**：通过 LangGraph 工作流图编排完整的自动化审校与结果打标流程。
