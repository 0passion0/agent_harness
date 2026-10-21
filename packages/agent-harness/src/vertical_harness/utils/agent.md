# 辅助工具模块 (vertical_harness/utils) - Agent 指南与规范

## 1. 目录定位与职责
`vertical_harness/utils/` 汇聚框架内部使用的轻量级通用工具类。
提供终端/UI 友好渲染展示辅助以及请求 Token 用量核算与成本估算基础设施。

## 2. 核心类与文件
- `__init__.py`：统一导出工具类 `StructuredFormatter`、`UsageEstimator`。
- `structured_formatter.py` (`StructuredFormatter`)：
  - 结构化数据展示格式化工具：提供差异高亮（Diff）、表格渲染、状态标签及代码块转义，提升控制台与桌面端交互可读性。
- `usage.py` (`UsageEstimator`)：
  - Token 消耗与推理成本估算器：统计输入/输出 Token 数量，按模型定价策略估算任务执行开销。

## 3. 关键交互与设计约束
1. **纯函数式与无副作用**：
   - 工具方法应保持逻辑纯粹、无隐式全局状态依赖，避免引入复杂的外部环境依赖。
2. **极简主义（Ponytail 原则）**：
   - 优先使用 Python 标准库实现字符串处理与数学计算，杜绝引入重型第三方格式化或分析依赖。
3. **强类型与边界鲁棒**：
   - 估算与格式化逻辑必须严格校验输入参数类型，对空值、非标准格式入参 Fail-Fast 或提供安全兜底，不抛未预期系统异常。
