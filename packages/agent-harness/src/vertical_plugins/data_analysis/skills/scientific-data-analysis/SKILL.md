---
name: data-analysis-scientific-data-analysis
description: 表格数据画像（CSV/Parquet/XLSX）、确定性统计分析、产物哈希审计与中文 Markdown 研究报告生成。基于 DataAnalysisWorkflow 六阶段固定 LangGraph 流程与用户授权执行机制。
metadata:
  include_tools: data_analysis_inspect_table, data_analysis_ask_researcher, data_analysis_finalize_report
---

# 科研数据分析技能规范 (Scientific Data Analysis Skill)

本技能定义 `data_analysis` 插件的能力范围、六阶段固定工作流与执行授权要求。主 Agent 在初始化阶段发现本技能说明，通过 `task` 提交数据分析工作流（`data_analysis_workflow`）请求；需要授权时，由宿主网关暂停执行并展示用户确认，批准后才开始运行。

---

## 1. 适用范围与授权边界

1. **支持格式**：工作区内的 `.csv`、`.parquet`、`.xlsx` 表格数据。
2. **执行授权**：用户要求执行且输入已明确时，调用 `task`，设置 `subagent_type="data_analysis_workflow"`，在 `description` 中提供 JSON 输入（含 `file_path` 及本次操作参数）。宿主负责展示插件名称、操作目的、输入范围与计划输出，并按授权状态决定确认或执行。仅在聊天中询问是否授权不会创建运行请求；用户仅要求讨论方案时不发起运算。
3. **真实计算与证据闭环**：默认执行全表描述性统计；相关分析（`correlation`）与组间比较（`group_comparison`）需提供显式列名参数。严禁在缺少参数时编造统计检验结论或虚构随机种子说明。
4. **先读取真实变量**：用户要求先识别表格变量时，先提交 `{"task_type":"profile","file_path":"工作区内的实际文件路径"}`，已知工作表时同时提供 `sheet_name`。文件路径使用工作区相对路径或 `/workspace/` 前缀路径；若文件工具列出 `/data.xlsx`，提交 `data.xlsx`。多工作表需根据返回结果确认工作表，列画像有分页时按 `next_column_offset` 继续获取。根据真实画像划分变量角色和分析方向，不能用拟定列名代替读取结果。画像模式仅返回画像，不生成统计分析报告。
5. **能力边界**：当前统计计算仅支持描述性统计、Pearson 相关与组间比较；尚不支持多变量 logistic 回归、Firth、MICE 或限制性立方样条（RCS）。可解释这些方法为何适合研究问题，但必须明确当前插件无法执行，不能将其列为已具备执行能力的待办，也不能用其他分析类型替代。

---

## 2. 六个固定执行阶段 (`DataAnalysisWorkflow.run()`)

1. **`validate_input`**：校验输入文件、工作区路径边界、数据格式及分析目标；缺少必要参数时请求用户补充。
2. **`profile_table`**：读取 CSV、Parquet、XLSX，完成行列数、变量类型、缺失值、数值分位数及分类频次画像（支持大表分批处理）。
3. **`analyze_data`**：根据明确的分析目标执行真实计算，生成 `scripts/analyze_data.py` 与 `artifacts/analysis_summary.json`，记录 SHA256 执行凭证。
4. **`audit_results`**：核验分析脚本与 JSON 产物真实存在、SHA256 哈希一致、无 `NaN`/`Infinity` 且行列指标与画像一致。
5. **`generate_report`**：基于真实画像与计算结果生成中文 Markdown 报告（含 `研究目标`、`数据概况`、`分析方法`、`分析结果`、`讨论与局限`、`结论`）。
6. **`publish_report`**：验证报告与数据产物的一致性，原子保存 `artifacts/report.md` 与 `artifacts/report_validation.json`。

---

## 3. 受控节点干预与状态观察 (`PluginRuntime`)

- **按需状态观察**：通过 `runtime.get_progress(thread_id)`、`runtime.get_node_summary(thread_id, node_name)` 查看阶段进度与摘要，不在对话中直接倾倒整表或全量内部状态。
- **受控节点干预**：通过 `runtime.modify_node_input(thread_id, node_name, updates)`、`runtime.retry_node(thread_id, node_name)`、`runtime.fork_branch(thread_id, fork_thread_id, from_node=...)` 在白名单字段与 Pydantic 校验下从检查点重算下游节点，禁止增删或重排工作流节点。
