"""科研数据分析插件（data_analysis）、六阶段声明式工作流、真实统计计算、Hook 干预与按需观察测试模块。"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from openpyxl import Workbook

import vertical_plugins.data_analysis as data_analysis
from vertical_harness.config.settings import HarnessSettings
from vertical_harness.engine.compose import AgentComposer
from vertical_harness.engine.plugin_loader import discover_plugins, discover_workflow_graphs
from vertical_plugins.common import (
    BuildServices,
    PluginContribution,
    PluginRuntime,
    SideEffect,
    WorkflowHooks,
    WorkflowMode,
    WorkflowPlugin,
)
from vertical_plugins.data_analysis import (
    ANALYSIS_INTERPRETATION_PROMPT,
    ANALYSIS_PLAN_PROMPT,
    CORE_REPORT_SECTIONS,
    DATA_ANALYSIS_WORKFLOW_NAME,
    REPORT_GENERATION_PROMPT,
    AnalysisEvidence,
    DataAnalysisInputState,
    DataAnalysisInternalState,
    DataAnalysisOutputState,
    DataAnalysisPlugin,
    DataAnalysisToolkit,
    DataAnalysisWorkflow,
    InternalState,
    artifact_paths,
    build_analysis_plan_prompt,
    build_report_prompt,
    input_schema,
    output_schema,
    profile_table,
    save_report,
    snapshot,
)


def test_data_analysis_plugin_discovery_and_manifest(tmp_path: Path) -> None:
    """验证插件发现、清单元数据、写副作用标识、默认不暴露全部工具及无全局中间件污染。"""
    # 第一步：通过 Entry Points 发现已安装的 data_analysis 插件
    plugins = discover_plugins(["data_analysis"])
    assert len(plugins) == 1
    plugin = plugins[0]
    assert isinstance(plugin, WorkflowPlugin)

    # 第二步：核对插件清单元数据
    assert plugin.manifest.plugin_id == "data_analysis"
    assert plugin.manifest.version == "0.1.0"
    assert len(plugin.manifest.description) >= 8

    # 第三步：构建默认插件贡献（P5：默认仅暴露 SKILL.md 与受控工作流，不直接暴露全部工具，不注入全局中间件）
    services = BuildServices(workspace_root=tmp_path)
    contribution = plugin.build(services)
    assert isinstance(contribution, PluginContribution)
    assert len(contribution.workflows) == 1
    assert contribution.workflows[0].name == DATA_ANALYSIS_WORKFLOW_NAME
    assert contribution.workflows[0].effect == SideEffect.WRITE
    assert len(contribution.tools) == 0
    assert len(contribution.skills) == 1
    assert len(contribution.middleware) == 0
    assert "data_analysis_workflow" in contribution.instructions

    # 第四步：核对 AgentComposer 默认注册与显式工具开启模式
    settings = HarnessSettings(_env_file=None, model="test:test", workspace_root=tmp_path)
    composer = AgentComposer(settings)
    composer.register_plugins(plugins, services)
    assert DATA_ANALYSIS_WORKFLOW_NAME in composer.workflows
    assert len(composer.eager_tools) == 0
    assert len(composer.plugin_middleware) == 0

    with_tools_plugin = DataAnalysisPlugin(include_tools=True)
    with_tools_contrib = with_tools_plugin.build(services)
    assert len(with_tools_contrib.tools) == 3


def test_data_analysis_workflow_three_layer_contract_and_six_stages(tmp_path: Path) -> None:
    """验证工作流遵循三层独立状态架构，且 run() 清晰声明六个独立 LangGraph 节点。"""
    assert InternalState is DataAnalysisInternalState
    assert input_schema is DataAnalysisInputState
    assert output_schema is DataAnalysisOutputState

    assert DataAnalysisInternalState is not DataAnalysisInputState
    assert DataAnalysisInternalState is not DataAnalysisOutputState
    assert DataAnalysisInputState is not DataAnalysisOutputState

    # 验证 run() 声明的六个固定阶段均被编译为独立 LangGraph 节点
    workflow = DataAnalysisWorkflow(workspace_root=tmp_path)
    plan = workflow.run()
    expected_nodes = (
        "validate_input",
        "profile_table",
        "analyze_data",
        "audit_results",
        "generate_report",
        "publish_report",
    )
    assert plan.node_names == expected_nodes
    compiled = workflow.compile()
    for node_name in expected_nodes:
        assert node_name in compiled.nodes

    # 验证规范转换与写操作副作用标识
    contribution = workflow.to_workflow_contribution()
    assert contribution.effect == SideEffect.WRITE
    assert contribution.requires_approval is True
    compiled_spec = contribution.to_subagent_spec(preferred_mode=WorkflowMode.COMPILED)
    assert compiled_spec["name"] == DATA_ANALYSIS_WORKFLOW_NAME
    assert "runnable" in compiled_spec

    async_spec = contribution.to_subagent_spec(preferred_mode=WorkflowMode.ASYNC)
    assert async_spec == {
        "name": DATA_ANALYSIS_WORKFLOW_NAME,
        "description": contribution.description,
        "graph_id": DATA_ANALYSIS_WORKFLOW_NAME,
        "mode": "isolated",
    }

    graphs = discover_workflow_graphs(["data_analysis"])
    assert DATA_ANALYSIS_WORKFLOW_NAME in graphs
    assert isinstance(graphs[DATA_ANALYSIS_WORKFLOW_NAME], CompiledStateGraph)
    assert isinstance(data_analysis.graph, CompiledStateGraph)


def test_table_profile_csv_and_pagination(tmp_path: Path) -> None:
    """测试 CSV 数据表全量行扫描、分位数计算与列分页画像。"""
    csv_file = tmp_path / "patients.csv"
    headers = ["patient_id", "age", "bmi", "category", "notes"]
    rows = [
        ["P001", "25", "21.5", "Control", "Normal"],
        ["P002", "30", "24.0", "Case", "Normal"],
        ["P003", "45", "", "Case", "Overweight"],
        ["P004", "60", "28.3", "Control", "Elderly"],
        ["P005", "35", "22.1", "Control", ""],
    ]
    with csv_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

    profile = profile_table(csv_file, run_dir=tmp_path, column_offset=0)
    assert profile["row_count"] == 5
    assert profile["column_count"] == 5
    assert len(profile["columns"]) == 5
    assert profile["next_column_offset"] is None

    age_col = next(c for c in profile["columns"] if c["name"] == "age")
    assert age_col["numeric_count"] == 5
    assert age_col["missing_count"] == 0
    assert age_col["numeric_subset"]["min"] == 25.0
    assert age_col["numeric_subset"]["max"] == 60.0

    bmi_col = next(c for c in profile["columns"] if c["name"] == "bmi")
    assert bmi_col["missing_count"] == 1
    assert bmi_col["numeric_count"] == 4

    cached = profile_table(csv_file, run_dir=tmp_path, column_offset=0)
    assert cached["row_count"] == 5


def test_table_profile_xlsx(tmp_path: Path) -> None:
    """测试 XLSX 数据表多工作表发现与全表画像。"""
    xlsx_file = tmp_path / "study.xlsx"
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "Overview"
    ws1.append(["Title", "Date"])
    ws1.append(["Clinical Trial Phase 2", "2026-10-19"])

    ws2 = wb.create_sheet(title="Biomarkers")
    ws2.append(["subject_id", "crp_level", "hba1c"])
    ws2.append(["S01", 1.2, 5.4])
    ws2.append(["S02", 3.8, 6.1])
    ws2.append(["S03", 0.9, 5.2])
    wb.save(xlsx_file)

    sheet_discovery = profile_table(xlsx_file, run_dir=tmp_path)
    assert sheet_discovery["sheet_names"] == ["Overview", "Biomarkers"]
    assert sheet_discovery["next_action"] == "Choose sheet_name to scan all rows"

    bio_profile = profile_table(xlsx_file, run_dir=tmp_path, sheet_name="Biomarkers")
    assert bio_profile["row_count"] == 3
    assert bio_profile["column_count"] == 3
    crp_col = next(c for c in bio_profile["columns"] if c["name"] == "crp_level")
    assert crp_col["numeric_count"] == 3
    assert crp_col["numeric_subset"]["min"] == 0.9


def test_report_validation_and_atomic_save(tmp_path: Path) -> None:
    """测试核心章节中文报告校验、证据哈希匹配与原子发布落盘。"""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    script_file = scripts_dir / "analyze.py"
    script_file.write_text("print('running analysis')", encoding="utf-8")

    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    result_file = artifacts_dir / "metrics.json"
    result_file.write_text(json.dumps({"mean_diff": 1.45}), encoding="utf-8")

    evidence = AnalysisEvidence(tmp_path)
    with script_file.open("rb") as f:
        script_sha = hashlib.file_digest(f, "sha256").hexdigest()
    with result_file.open("rb") as f:
        real_result_sha = hashlib.file_digest(f, "sha256").hexdigest()

    evidence_receipt = {
        "execution_id": "test-exec-1",
        "script": "scripts/analyze.py",
        "script_sha256": script_sha,
        "exit_code": 0,
        "artifact_sha256": {"artifacts/metrics.json": real_result_sha},
    }
    evidence.save([evidence_receipt])

    valid_markdown = """
# 临床标志物统计分析报告

## 研究目标
评估生物标志物的分布特征及组间差异。

## 数据概况
样本包含 500 例受试者临床记录。

## 分析方法
执行描述性统计与组间均值对比，参见 [分析脚本](../scripts/analyze.py)。

## 分析结果
组间均值差为 1.45，详见 [计算结果](metrics.json)。

## 讨论与局限
本数据为观察性样本，未执行参数假设检验，不推导因果结论。

## 结论
已完成确定性统计汇总与证据核验。
"""

    saved = save_report(
        markdown=valid_markdown,
        evidence_files=["scripts/analyze.py", "artifacts/metrics.json"],
        run_dir=tmp_path,
        executions=evidence.receipts,
    )
    assert saved.is_file()
    assert (tmp_path / "artifacts" / "report.md").is_file()
    assert (tmp_path / "artifacts" / "report_validation.json").is_file()

    # 缺失核心章节时必须拒绝发布
    invalid_markdown = valid_markdown.replace("## 结论", "## 附录")
    with pytest.raises(ValueError, match="Missing report sections: 结论"):
        save_report(
            markdown=invalid_markdown,
            evidence_files=["scripts/analyze.py", "artifacts/metrics.json"],
            run_dir=tmp_path,
            executions=evidence.receipts,
        )


def test_data_analysis_workflow_execution_profile_and_validation(tmp_path: Path) -> None:
    """测试数据分析工作流画像模式、缺少必要参数拦截以及未经参数支撑的统计检验拦截。"""
    csv_file = tmp_path / "sample.csv"
    csv_file.write_text("id,score,group\n1,95,A\n2,88,B\n3,72,A\n", encoding="utf-8")

    checkpointer = InMemorySaver()
    workflow = DataAnalysisWorkflow(workspace_root=tmp_path, checkpointer=checkpointer)

    # 1. 画像模式执行
    output = workflow.invoke(
        {
            "messages": [HumanMessage(content=f"请画像：{csv_file.name}")],
            "file_path": "sample.csv",
            "task_type": "profile",
        },
        config={"configurable": {"thread_id": "profile-thread-1"}},
        durability="sync",
    )
    assert set(output.keys()) <= {"messages", "structured_response"}
    response = output["structured_response"]
    assert response["status"] == "passed"
    assert response["profile"]["row_count"] == 3
    assert response["profile"]["column_count"] == 3

    # 2. 缺少必要文件或输入载荷时请求用户补充，禁止自行假设
    bad_out = workflow.invoke(
        {"messages": [HumanMessage(content="仅是一段普通问候，没有任何文件路径")]},
        config={"configurable": {"thread_id": "invalid-thread-1"}},
        durability="sync",
    )
    assert bad_out["structured_response"]["status"] == "invalid_input"

    # 3. 请求组间比较但缺少 group_column 时必须拒绝假设，同时在默认描述统计下若提及 t检验/p值 则明确标注不编造推断结论
    missing_group_col = workflow.invoke(
        {
            "messages": [HumanMessage(content="请做组间比较")],
            "file_path": "sample.csv",
            "analysis_type": "group_comparison",
        },
        config={"configurable": {"thread_id": "missing-group-col-thread"}},
        durability="sync",
    )
    assert missing_group_col["structured_response"]["status"] == "invalid_input"
    assert "group_column" in missing_group_col["structured_response"]["error"]

    unparam_inference = workflow.invoke(
        {
            "messages": [HumanMessage(content="请做 t检验 计算 p值 显著性")],
            "file_path": "sample.csv",
            "research_question": "分析两组 score 的 t检验 p值 显著性差异",
        },
        config={"configurable": {"thread_id": "unparam-inference-thread"}},
        durability="sync",
    )
    assert unparam_inference["structured_response"]["status"] == "passed"
    assert "不自动假设或编造统计检验结论" in (
        unparam_inference["structured_response"]["analysis"]["inference_note"] or ""
    )


def test_data_analysis_workflow_real_calculations_and_honest_report(tmp_path: Path) -> None:
    """测试六阶段全流程的真实计算（描述统计、相关分析、组间比较）及诚实中文报告输出。"""
    csv_file = tmp_path / "clinical_cohort.csv"
    csv_file.write_text(
        "patient_id,biomarker_a,biomarker_b,group\n"
        "P01,10.0,20.0,Control\n"
        "P02,20.0,40.0,Control\n"
        "P03,30.0,60.0,Treatment\n"
        "P04,40.0,80.0,Treatment\n",
        encoding="utf-8",
    )

    checkpointer = InMemorySaver()
    workflow = DataAnalysisWorkflow(workspace_root=tmp_path, checkpointer=checkpointer)

    # 1. 默认描述性统计全流程
    desc_out = workflow.invoke(
        {
            "messages": [HumanMessage(content="对临床队列执行描述性统计分析")],
            "file_path": "clinical_cohort.csv",
            "research_question": "汇总 biomarker_a 与 biomarker_b 的基本分布特征",
        },
        config={"configurable": {"thread_id": "desc-thread"}},
        durability="sync",
    )
    resp = desc_out["structured_response"]
    assert resp["status"] == "passed"
    assert resp["analysis"]["analysis_type"] == "descriptive"
    assert resp["analysis"]["row_count"] == 4
    assert resp["analysis"]["descriptive_statistics"]["biomarker_a"]["mean"] == pytest.approx(25.0)
    assert resp["analysis"]["descriptive_statistics"]["biomarker_b"]["mean"] == pytest.approx(50.0)
    assert resp["audit"]["status"] == "passed"
    assert resp["report"]["status"] == "passed"

    report_content = (tmp_path / "artifacts" / "report.md").read_text(encoding="utf-8")
    for section in CORE_REPORT_SECTIONS:
        assert f"## {section}" in report_content
    # 严禁包含未经验证的虚假声称
    assert "已固定随机种子" not in report_content
    assert "已执行统计检验" not in report_content

    # 2. 显式相关分析（Pearson 相关系数真实计算）
    corr_out = workflow.invoke(
        {
            "messages": [HumanMessage(content="计算 biomarker_a 与 biomarker_b 的相关性")],
            "file_path": "clinical_cohort.csv",
            "analysis_type": "correlation",
            "target_columns": ["biomarker_a", "biomarker_b"],
            "research_question": "评估 biomarker_a 与 biomarker_b 的线性相关程度",
        },
        config={"configurable": {"thread_id": "corr-thread"}},
        durability="sync",
    )
    corr_resp = corr_out["structured_response"]
    assert corr_resp["status"] == "passed"
    assert corr_resp["analysis"]["analysis_type"] == "correlation"
    pairs = corr_resp["analysis"]["correlations"]
    assert len(pairs) == 1
    assert pairs[0]["pearson_r"] == pytest.approx(1.0)
    assert pairs[0]["n"] == 4

    # 3. 显式组间比较（按 group 分组真实统计均值差与效应量）
    grp_out = workflow.invoke(
        {
            "messages": [HumanMessage(content="按 group 比较 biomarker_a")],
            "file_path": "clinical_cohort.csv",
            "analysis_type": "group_comparison",
            "group_column": "group",
            "target_columns": ["biomarker_a"],
            "research_question": "比较 Control 与 Treatment 组的 biomarker_a 水平差异",
        },
        config={"configurable": {"thread_id": "group-thread"}},
        durability="sync",
    )
    grp_resp = grp_out["structured_response"]
    assert grp_resp["status"] == "passed"
    assert grp_resp["analysis"]["analysis_type"] == "group_comparison"
    comp = grp_resp["analysis"]["group_comparisons"][0]
    assert comp["target_column"] == "biomarker_a"
    assert comp["groups"]["Control"]["mean"] == pytest.approx(15.0)
    assert comp["groups"]["Treatment"]["mean"] == pytest.approx(35.0)


def test_data_analysis_hooks_and_plugin_runtime_intervention(tmp_path: Path) -> None:
    """测试 P6/P7 统一 Hook、节点级输入修改校验、从目标节点前检查点重试、分叉及按需状态观察。"""
    csv_file = tmp_path / "hook_cohort.csv"
    csv_file.write_text(
        "id,val_a,val_b,grp\n1,10,100,G1\n2,20,200,G1\n3,30,300,G2\n4,40,400,G2\n",
        encoding="utf-8",
    )

    observed_events: list[str] = []
    before_run_called = False
    after_run_called = False
    before_publish_called = False

    def _on_before_run(state: dict[str, Any], ctx: Any) -> dict[str, Any] | None:
        nonlocal before_run_called
        before_run_called = True
        return None

    def _on_after_node(event: Any, ctx: Any) -> None:
        observed_events.append(f"{event.node_name}:{event.status}")

    def _on_before_publish(state: dict[str, Any], ctx: Any) -> bool:
        nonlocal before_publish_called
        before_publish_called = True
        return True

    def _on_after_run(state: dict[str, Any], ctx: Any) -> None:
        nonlocal after_run_called
        after_run_called = True

    hooks = WorkflowHooks(
        before_run=_on_before_run,
        after_node=_on_after_node,
        before_publish=_on_before_publish,
        after_run=_on_after_run,
    )
    plugin = DataAnalysisPlugin()
    runtime: PluginRuntime = plugin.create_runtime(
        workspace_root=tmp_path,
        checkpointer=InMemorySaver(),
        hooks=hooks,
    )

    thread_id = "runtime-thread-1"
    out = runtime.invoke(
        {
            "messages": [HumanMessage(content="执行全流程分析")],
            "file_path": "hook_cohort.csv",
            "research_question": "初始描述性分析",
        },
        thread_id=thread_id,
    )
    assert out["structured_response"]["status"] == "passed"
    assert before_run_called is True
    assert before_publish_called is True
    assert after_run_called is True
    assert observed_events == [
        "validate_input:completed",
        "profile_table:completed",
        "analyze_data:completed",
        "audit_results:completed",
        "generate_report:completed",
        "publish_report:completed",
    ]

    # 1. P7 第一层与第二层按需观察：获取轻量运行进度与单节点精简摘要（不暴露大表全量数据）
    progress = runtime.get_progress(thread_id)
    assert progress.status == "completed"
    assert tuple(progress.completed_nodes) == (
        "validate_input",
        "profile_table",
        "analyze_data",
        "audit_results",
        "generate_report",
        "publish_report",
    )
    node_obs = runtime.get_node_summary(thread_id, "analyze_data")
    assert node_obs.node_name == "analyze_data"
    assert node_obs.status == "completed"
    assert "scripts/analyze_data.py" in node_obs.artifact_refs

    # 2. P6 节点输入修改校验：禁止修改未开放字段或受保护的 InternalState 字段
    with pytest.raises(ValueError, match="Cannot modify protected InternalState fields"):
        runtime.modify_node_input(
            thread_id,
            "analyze_data",
            {"analysis_executions": []},
        )
    with pytest.raises(ValueError, match="not editable on node"):
        runtime.modify_node_input(
            thread_id,
            "analyze_data",
            {"file_path": "other.csv"},
        )

    # 3. P6 修改 analyze_data 节点白名单输入字段，并从 analyze_data 节点前检查点重试或分叉
    fork_thread_id = "runtime-thread-forked"
    fork_cfg = runtime.fork_branch(
        thread_id,
        "analyze_data",
        new_thread_id=fork_thread_id,
        updates={
            "analysis_type": "correlation",
            "target_columns": ["val_a", "val_b"],
            "research_question": "分叉后计算 val_a 与 val_b 的相关性",
        },
    )
    assert fork_cfg["configurable"]["thread_id"] == fork_thread_id

    fork_out = runtime.retry_node(fork_thread_id, "analyze_data")
    assert fork_out["structured_response"]["status"] == "passed"
    assert fork_out["structured_response"]["analysis"]["analysis_type"] == "correlation"
    assert fork_out["structured_response"]["analysis"]["correlations"][0]["pearson_r"] == pytest.approx(1.0)

    # 验证原分支检查点状态未被分叉覆盖
    orig_debug = runtime.inspect_debug_state(thread_id)
    assert orig_debug["values"]["analysis_result"]["analysis_type"] == "descriptive"
    assert len(runtime.get_audit_log(thread_id)) >= 4


def test_desktop_snapshot_and_artifact_paths(tmp_path: Path) -> None:
    """测试桌面客户端快照与产物投影函数。"""
    report_file = tmp_path / "artifacts" / "report.md"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text("## 报告正文", encoding="utf-8")

    state_values: dict[str, Any] = {
        "active_plugin": "data_analysis",
        "analysis_executions": [{"execution_id": "1", "script": "test.py"}],
        "analysis_report_path": str(report_file),
    }

    res_snapshot = snapshot(state_values, tmp_path)
    assert res_snapshot["report_open_path"] == "/workspace/artifacts/report.md"
    assert len(res_snapshot["analysis_executions"]) == 1

    paths = artifact_paths(state_values, tmp_path)
    assert paths == ["/workspace/artifacts/report.md"]

    inactive_state: dict[str, Any] = {"active_plugin": "other_plugin"}
    assert artifact_paths(inactive_state, tmp_path) == []


def test_data_analysis_toolkit_and_streamlined_prompts(tmp_path: Path) -> None:
    """测试精简后的 DataAnalysisToolkit 与提示词模块（P3：已移除冗余 guides 间接层）。"""
    toolkit = DataAnalysisToolkit(workspace_root=tmp_path)
    tools = {t.name: t for t in toolkit.get_tools()}

    # 1. 验证移除 data_analysis_get_node_prompt 后保留的 3 个核心工具
    assert "data_analysis_get_node_prompt" not in tools
    assert "data_analysis_inspect_table" in tools
    assert "data_analysis_ask_researcher" in tools
    assert "data_analysis_finalize_report" in tools

    # 2. 验证精简后的 analysis.py 与 report.py 提示词构造函数
    assert len(ANALYSIS_PLAN_PROMPT) > 20
    assert len(ANALYSIS_INTERPRETATION_PROMPT) > 20
    assert len(REPORT_GENERATION_PROMPT) > 20
    plan_prompt = build_analysis_plan_prompt(
        analysis_goal="比较两组指标",
        analysis_type="group_comparison",
        profile_summary={"row_count": 20},
    )
    assert "group_comparison" in plan_prompt
    rep_prompt = build_report_prompt(
        research_question="比较两组指标",
        profile_summary={"row_count": 20},
        analysis_summary={"status": "passed"},
        audit_summary={"status": "passed"},
    )
    assert "比较两组指标" in rep_prompt

    # 3. 验证冗余 guides 导出已彻底移除
    assert not hasattr(data_analysis, "GUIDE_DESCRIPTIONS")
    assert not hasattr(data_analysis, "read_analysis_guide")


def test_data_analysis_workflow_with_optional_llm_model(tmp_path: Path) -> None:
    """测试在 generate_report 阶段结合可选绑定语言模型生成符合事实的中文报告。"""
    fake_report = (
        "# 队列统计分析报告\n\n"
        "## 研究目标\n评估指标 a 与 b 的分布特征。\n\n"
        "## 数据概况\n共纳入 2 行有效记录。\n\n"
        "## 分析方法\n采用确定性描述统计，详见 [脚本](../scripts/analyze_data.py)。\n\n"
        "## 分析结果\n汇总指标见 [结果](analysis_summary.json)。\n\n"
        "## 讨论与局限\n样本量较小，仅作描述性汇总。\n\n"
        "## 结论\n分析完成并通过核验。\n"
    )
    fake_model = GenericFakeChatModel(
        messages=iter(
            [
                AIMessage(content="已完成指标 a 与 b 的统计计算摘要解释。"),
                AIMessage(content=fake_report),
            ]
        )
    )

    csv_file = tmp_path / "study_data.csv"
    csv_file.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")

    workflow = DataAnalysisWorkflow(
        workspace_root=tmp_path,
        model=fake_model,
        checkpointer=InMemorySaver(),
    )
    output = workflow.invoke(
        {
            "messages": [HumanMessage(content="执行分析并生成报告")],
            "file_path": "study_data.csv",
            "research_question": "评估指标 a 与 b 的分布特征",
        },
        config={"configurable": {"thread_id": "llm-report-thread"}},
        durability="sync",
    )
    assert output["structured_response"]["status"] == "passed"
    saved_text = (tmp_path / "artifacts" / "report.md").read_text(encoding="utf-8")
    assert "队列统计分析报告" in saved_text
