# 技能资产根目录 (research_citation/skills) - Agent 指南与规范

## 目录定位与职责
`research_citation/skills/` 存放插件随附导出的所有智能体技能（Skills）定义与静态资产。供 Deep Agents 的 `SkillsMiddleware` 自动加载并注入到智能体的虚拟文件系统中。

## 子技能结构
- `numeric-citation-audit/`：数字标号引文审计技能，包含 `SKILL.md` 指导模型何时以及如何调用审计工作流与工具。

## Agent 交互与资产规范
1. **沙箱只读**：被挂载到智能体环境（如 `/skills/`）后，所有技能文件均受只读权限保护，禁止智能体篡改。
2. **规范文件头**：每个子技能目录下必须包含 `SKILL.md`，并带有合法的 YAML Frontmatter（定义 `name` 与 `description`）。
3. **精准提示**：技能文档需清晰指导模型在遇到引文核验需求时选用对应工具，避免模型幻觉。
