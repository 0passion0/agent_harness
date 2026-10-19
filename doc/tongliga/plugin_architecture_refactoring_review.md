# Agent Harness 插件架构精简重构代码审查报告

- **审查人 / 作者**：`tongliga`
- **日期**：`2026-10-19`
- **涉及模块**：
  - `packages/agent-harness/src/vertical_plugins/common`
  - `packages/agent-harness/src/vertical_plugins/data_analysis`
  - `packages/agent-harness/src/vertical_plugins/research_citation`
  - `packages/agent-harness/src/vertical_harness/engine`
  - `scripts/check_wheels.py`
  - `tests/test_data_analysis.py`、`tests/test_supervisor_workflows_and_time_travel.py`、`tests/test_schema.py`、`tests/test_stabilization.py`、`tests/test_workflow_logging.py`

---

## 1. 五问认知闭环审计（Five-Question Loop）

1. **输入与输出契约（Input & Output）**：
   - 所有工作流统一继承 `BaseWorkflow`，严格维护 `input_schema`、`output_schema` 与 `InternalState` 三层隔离状态 Schema。
   - `DataAnalysisWorkflow.run()` 直接在方法内声明 `validate_input → profile_table → analyze_data → audit_results → generate_report → publish_report` 六个独立 LangGraph 节点；`CitationAuditWorkflow.run()` 直接在方法内声明 `validate_input → check_citations → build_report` 三个独立 LangGraph 节点。
   - 跨模块输入、授权请求与进度观测均由 Pydantic 严格模型（`StrictModel`，`extra="forbid"`）校验。
2. **爆炸半径审计（Blast Radius Audit）**：
   - 保留 `DomainPlugin`、`PluginManifest`、`PluginContribution`、`WorkflowContribution` 与 Entry Points 自动发现机制的完全兼容。
   - 保持单向依赖：`vertical_plugins/*` 仅依赖 `vertical_plugins.common`，绝不逆向导入 `vertical_harness`；宿主级 `PluginAuthorizationGateway` 位于 `vertical_harness/engine/policy.py` 并通过 `AuthorizationGuardProtocol` 协议注入。
3. **极简主义审计（YAGNI / Ponytail Audit）**：
   - 删除了 `vertical_plugins/data_analysis/guides/`（7 个冗余 Markdown 指南文件）及 `GUIDE_DESCRIPTIONS`、`read_analysis_guide()`、`data_analysis_get_node_prompt`、`prompts/nodes.py`、`prompts/instructions.py`、`runtime/`、`research_citation/prompts/` 等重复包装与间接层。
   - 提取 `WorkflowPlugin`、`BaseWorkflow`、`WorkflowContext`、`WorkflowPlan`、`WorkflowHooks`、`PluginRuntime` 到 `vertical_plugins/common`，消除了两个插件在技能扫描、状态图编译、检查点分叉与运行管理上的重复代码。
4. **契约合规性（Contract Compliance）**：
   - 内部契约遵循 Fail-Fast 与 Fail-Closed 原则：缺少必要参数、请求未提供显式参数的统计检验、尝试修改 `InternalState` 受保护字段或伪造 `approved=True` 均立即拒绝。
   - 取消 `AnalysisEvidence` 对主 Agent 的全局中间件污染，仅对已授权的分析工作流运行记录真实脚本与产物 SHA256 凭证。
5. **实证验证（Evidence-Based Verification）**：
   - 在项目本地虚拟环境 `.venv` 下执行 `ruff check`、`mypy` 与 `pytest` 全量验证通过（Exit Code 0）。

---

## 2. 三轴代码审查矩阵（Standards / Spec / Complexity）

| 审查维度 | 检查项 | 结论 | 说明 |
| :--- | :--- | :--- | :--- |
| **Standards（规范合规）** | 中文注释与 reST Docstring | 通过 | 全部新增与重构代码包含完整中文注释、`# 第一步：...` 步骤说明及包含 `:author: tongliga`、`:date: 2026-10-19` 的 reST docstring。 |
| **Standards（规范合规）** | 延迟图构建与导入无副作用 | 通过 | 删除模块导入时立即实例化并编译 `graph` 的行为，通过模块级 `__getattr__` 与 `get_data_analysis_graph()` / `get_citation_audit_graph()` 延迟构建。 |
| **Spec（需求契约）** | P1–P4 工作流模板与真实计算 | 通过 | `DataAnalysisWorkflow` 实现真实描述性统计、Pearson 相关分析与分组均值/Welch t 比较；报告仅引用已生成产物，严禁编造“随机种子已固定”或未经验证的推断结论。 |
| **Spec（需求契约）** | P5 插件三级权限与授权网关 | 通过 | 区分已安装、已启用、已授权；初始化仅暴露 `SKILL.md`；`PluginAuthorizationGateway` 维护 `discovered → pending_approval → authorized → running → completed/failed/cancelled` 状态机，拦截伪造 `approved=True` 及越权范围。 |
| **Spec（需求契约）** | P6–P7 节点级 Hook 与按需观察 | 通过 | `PluginRuntime` 支持 `before_run`、`before_node`、`after_node`、`on_error`、`before_publish`、`after_run` 及节点前检查点重算/分叉，提供进度摘要、节点摘要与调试详情三层按需观察。 |
| **Complexity（极简度）** | 目录收敛与重复消除 | 通过 | 严格收敛至 P8 目标目录结构；新增第三个 `WorkflowPlugin` 子类仅需声明 `manifest`、`workflow_cls` 与 `run()` 即可复用全部能力。 |

---

## 3. 终端实证验证结果（Terminal Verification Evidence）

所有验证均在项目本地虚拟环境 `.\.venv` 下执行：

1. **静态代码质量与类型检查（Ruff & Mypy）**：
   - `.\.venv\Scripts\python -m ruff check` -> **Exit Code 0**（`All checks passed!`）
   - `.\.venv\Scripts\python -m mypy` -> **Exit Code 0**（`Success: no issues found in 10 source files`）
2. **全量自动化测试套件（Pytest）**：
   - `.\.venv\Scripts\python -m pytest` -> **Exit Code 0**（`234 passed, 30 warnings in 28.42s`）
3. **独立分发包与 Wheel 资产检查（check_wheels.py）**：
   - `.\.venv\Scripts\python scripts/check_wheels.py --mode core-only` -> **Exit Code 0**（`Installed wheel: core-only native graph OK`）
   - `.\.venv\Scripts\python scripts/check_wheels.py --mode base` -> **Exit Code 0**（`Installed wheel: base install OK`）
   - `.\.venv\Scripts\python scripts/check_wheels.py --mode with-analysis` -> **Exit Code 0**（`Installed wheel with analysis extra: entry points, skills, prompts, type markers and native graphs OK`）
