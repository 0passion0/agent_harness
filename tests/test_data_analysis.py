"""科研数据分析插件（data_analysis）、LangGraph 工作流、三层状态契约、表画像、报告交付与 Hook 控制测试模块。"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from openpyxl import Workbook

import data_analysis
from data_analysis import (
    DATA_ANALYSIS_WORKFLOW_NAME,
    AnalysisEvidence,
    DataAnalysisInputState,
    DataAnalysisInternalState,
    DataAnalysisOutputState,
    DataAnalysisToolkit,
    DataAnalysisWorkflow,
    InternalState,
    artifact_paths,
    build_data_analysis_workflow,
    build_data_analysis_workflow_contribution,
    fork_node_branch,
    get_node_prompt,
    input_schema,
    inspect_node_checkpoints,
    modify_node_state,
    output_schema,
    profile_table,
    retry_node,
    save_report,
    snapshot,
)
from vertical_harness.config.settings import HarnessSettings
from vertical_harness.engine.compose import AgentComposer
from vertical_harness.plugins.contracts import (
    BuildServices,
    PluginContribution,
    WorkflowMode,
)
from vertical_harness.plugins.discovery import discover_plugins, discover_workflow_graphs


def test_data_analysis_plugin_discovery_and_manifest(tmp_path: Path) -> None:
    """验证插件发现、清单元数据、构建贡献与 AgentComposer 注册。"""
    # 第一步：通过插件 ID 发现已安装插件
    plugins = discover_plugins(["data_analysis"])
    assert len(plugins) == 1
    plugin = plugins[0]

    # 第二步：核对插件清单元数据
    assert plugin.manifest.plugin_id == "data_analysis"
    assert plugin.manifest.version == "0.1.0"
    assert len(plugin.manifest.description) >= 8

    # 第三步：构建插件贡献
    services = BuildServices(workspace_root=tmp_path)
    contribution = plugin.build(services)
    assert isinstance(contribution, PluginContribution)
    assert len(contribution.workflows) == 1
    assert contribution.workflows[0].name == DATA_ANALYSIS_WORKFLOW_NAME
    assert len(contribution.tools) == 4
    assert len(contribution.skills) == 1
    assert len(contribution.middleware) == 1
    assert "data_analysis_workflow" in contribution.instructions

    # 第四步：核对 AgentComposer 正常聚合
    settings = HarnessSettings(model="test:test")
    composer = AgentComposer(settings)
    composer.register_plugins(plugins, services)
    assert DATA_ANALYSIS_WORKFLOW_NAME in composer.workflows
    assert len(composer.eager_tools) == 4
    assert any(m.name == "AnalysisEvidence" for m in composer.plugin_middleware)


def test_data_analysis_workflow_three_layer_contract() -> None:
    """验证工作流遵循三层独立状态架构（InternalState / input_schema / output_schema）。"""
    assert InternalState is DataAnalysisInternalState
    assert input_schema is DataAnalysisInputState
    assert output_schema is DataAnalysisOutputState

    assert DataAnalysisInternalState is not DataAnalysisInputState
    assert DataAnalysisInternalState is not DataAnalysisOutputState
    assert DataAnalysisInputState is not DataAnalysisOutputState

    # 验证规范转换
    contribution = build_data_analysis_workflow_contribution()
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
    assert graphs[DATA_ANALYSIS_WORKFLOW_NAME] is data_analysis.graph


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

    # 首次扫描计算画像
    profile = profile_table(csv_file, run_dir=tmp_path, column_offset=0)
    assert profile["row_count"] == 5
    assert profile["column_count"] == 5
    assert len(profile["columns"]) == 5
    assert profile["next_column_offset"] is None

    # 检查 age 列的数值聚合
    age_col = next(c for c in profile["columns"] if c["name"] == "age")
    assert age_col["numeric_count"] == 5
    assert age_col["missing_count"] == 0
    assert age_col["numeric_subset"]["min"] == 25.0
    assert age_col["numeric_subset"]["max"] == 60.0

    # 检查 bmi 列的缺失值统计
    bmi_col = next(c for c in profile["columns"] if c["name"] == "bmi")
    assert bmi_col["missing_count"] == 1
    assert bmi_col["numeric_count"] == 4

    # 再次调用命中缓存
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

    # 未指定 sheet_name 时返回可用工作表列表
    sheet_discovery = profile_table(xlsx_file, run_dir=tmp_path)
    assert sheet_discovery["sheet_names"] == ["Overview", "Biomarkers"]
    assert sheet_discovery["next_action"] == "Choose sheet_name to scan all rows"

    # 指定 Biomarkers 工作表扫描
    bio_profile = profile_table(xlsx_file, run_dir=tmp_path, sheet_name="Biomarkers")
    assert bio_profile["row_count"] == 3
    assert bio_profile["column_count"] == 3
    crp_col = next(c for c in bio_profile["columns"] if c["name"] == "crp_level")
    assert crp_col["numeric_count"] == 3
    assert crp_col["numeric_subset"]["min"] == 0.9


def test_report_validation_and_atomic_save(tmp_path: Path) -> None:
    """测试科研中文报告七大二级标题校验、证据匹配与原子发布落盘。"""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    script_file = scripts_dir / "analyze.py"
    script_file.write_text("print('running analysis')", encoding="utf-8")

    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    result_file = artifacts_dir / "metrics.json"
    result_file.write_text(json.dumps({"p_value": 0.03, "effect_size": 1.45}), encoding="utf-8")

    evidence = AnalysisEvidence(tmp_path)
    evidence_receipt = {
        "execution_id": "test-exec-1",
        "script": "scripts/analyze.py",
        "script_sha256": "",
        "exit_code": 0,
        "artifact_sha256": {},
    }
    with script_file.open("rb") as f:
        evidence_receipt["script_sha256"] = hashlib.file_digest(f, "sha256").hexdigest()
    with result_file.open("rb") as f:
        real_result_sha = hashlib.file_digest(f, "sha256").hexdigest()
        evidence_receipt["artifact_sha256"] = {"artifacts/metrics.json": real_result_sha}

    evidence.save([evidence_receipt])

    valid_markdown = """
# 某临床标志物关联性研究报告

## 研究问题
探讨生物标志物与疾病发生的相关性与效应量大小。

## 数据说明
样本来源于队列研究，包含 500 例受试者临床数据。

## 分析方法
采用多因素 Logistic 回归模型进行调整分析。参见 [分析脚本](../scripts/analyze.py)。

## 结果
回归分析结果表明关键标志物显著相关，详见 [模型结果](metrics.json)。

## 讨论与局限
本研究为观察性队列，不能直接推导因果结论。

## 结论
该标志物具有潜在预测价值，值得进一步前瞻性验证。

## 复现信息
Python 3.12 环境，随机种子 42，依赖 duckdb 1.4。
"""

    # 1. 成功校验并发布报告
    saved = save_report(
        markdown=valid_markdown,
        evidence_files=["scripts/analyze.py", "artifacts/metrics.json"],
        run_dir=tmp_path,
        executions=evidence.receipts,
    )
    assert saved.is_file()
    assert (tmp_path / "artifacts" / "report.md").is_file()
    assert (tmp_path / "artifacts" / "report_validation.json").is_file()

    # 2. 缺失必需段落时拒绝发布
    invalid_markdown = valid_markdown.replace("## 复现信息", "## 其他信息")
    with pytest.raises(ValueError, match="Missing report sections: 复现信息"):
        save_report(
            markdown=invalid_markdown,
            evidence_files=["scripts/analyze.py", "artifacts/metrics.json"],
            run_dir=tmp_path,
            executions=evidence.receipts,
        )


def test_data_analysis_workflow_execution_profile(tmp_path: Path) -> None:
    """测试基于 LangGraph 的数据分析工作流执行表格画像。"""
    csv_file = tmp_path / "sample.csv"
    with csv_file.open("w", encoding="utf-8", newline="") as f:
        f.write("id,score\n1,95\n2,88\n3,72\n")

    checkpointer = InMemorySaver()
    workflow = build_data_analysis_workflow(workspace_root=tmp_path, checkpointer=checkpointer)

    config = {"configurable": {"thread_id": "profile-thread-1"}}
    output = workflow.invoke(
        {
            "messages": [HumanMessage(content=f"请分析表格：{csv_file}")],
            "file_path": str(csv_file),
            "task_type": "profile",
        },
        config=config,
        durability="sync",
    )

    # 验证遵循 DataAnalysisOutputState 契约
    assert set(output.keys()) <= {"messages", "structured_response"}
    assert "validated_input" not in output
    assert "validation_error" not in output

    response = output["structured_response"]
    assert response["status"] == "passed"
    assert "profile" in response
    assert response["profile"]["row_count"] == 3
    assert response["profile"]["column_count"] == 2


def test_data_analysis_workflow_invalid_input(tmp_path: Path) -> None:
    """测试工作流非法或空输入时的优雅拦截与结构化错误响应。"""
    checkpointer = InMemorySaver()
    workflow = build_data_analysis_workflow(workspace_root=tmp_path, checkpointer=checkpointer)
    output = workflow.invoke(
        {"messages": [HumanMessage(content="仅是一段普通问候，没有任何任务载荷")]},
        config={"configurable": {"thread_id": "invalid-thread"}},
        durability="sync",
    )
    assert output["structured_response"]["status"] == "invalid_input"
    assert "Missing data analysis parameters" in output["structured_response"]["error"]


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


def test_data_analysis_toolkit_and_node_prompts(tmp_path: Path) -> None:
    """测试 DataAnalysisToolkit 工具与固定节点系统提示词查询。"""
    toolkit = DataAnalysisToolkit(workspace_root=tmp_path)
    tools = {t.name: t for t in toolkit.get_tools()}

    # 1. 验证导出工具集合
    assert "data_analysis_get_node_prompt" in tools
    assert "data_analysis_inspect_table" in tools
    assert "data_analysis_ask_researcher" in tools
    assert "data_analysis_finalize_report" in tools

    # 2. 测试查询节点系统提示词
    read_prompt_tool = tools["data_analysis_get_node_prompt"]
    prompt_text = read_prompt_tool.invoke({"node_name": "profile_table"})
    assert "profile_table" in prompt_text
    assert "DuckDB" in prompt_text

    # 3. 测试纯函数 get_node_prompt
    for node in ("parse_and_validate_input", "profile_table", "validate_report", "finalize_output"):
        p = get_node_prompt(node)
        assert len(p) > 20

    with pytest.raises(KeyError, match="未知的分析节点"):
        get_node_prompt("nonexistent_node")


def test_data_analysis_workflow_hooks(tmp_path: Path) -> None:
    """测试主智能体监控与干预 Hook：审查检查点、修改状态、重试节点与分叉分支。"""
    csv_file = tmp_path / "test_hook.csv"
    csv_file.write_text("x,y\n10,20\n30,40\n", encoding="utf-8")

    checkpointer = InMemorySaver()
    workflow = build_data_analysis_workflow(workspace_root=tmp_path, checkpointer=checkpointer)
    thread_id = "hook-test-thread-1"

    # 1. 初始运行工作流
    config = {"configurable": {"thread_id": thread_id}}
    initial_output = workflow.invoke(
        {
            "messages": [HumanMessage(content="分析数据")],
            "file_path": str(csv_file),
            "task_type": "profile",
        },
        config=config,
        durability="sync",
    )
    assert initial_output["structured_response"]["status"] == "passed"

    # 2. Hook: 审查节点检查点历史 (inspect_node_checkpoints)
    summaries = inspect_node_checkpoints(workflow, thread_id)
    assert len(summaries) >= 1
    assert any(s.thread_id == thread_id for s in summaries)

    # 3. Hook: 修改节点状态 (modify_node_state)
    new_cfg = modify_node_state(
        workflow,
        thread_id=thread_id,
        values={"validation_error": "manual_intervention_error"},
        as_node="parse_and_validate_input",
    )
    assert "configurable" in new_cfg

    # 4. Hook: 分叉新建分支 (fork_node_branch)
    fork_thread_id = "hook-test-branch-2"
    forked_cfg = fork_node_branch(
        workflow,
        thread_id=thread_id,
        fork_thread_id=fork_thread_id,
        values={"task_type": "profile"},
    )
    assert forked_cfg["configurable"]["thread_id"] == fork_thread_id

    # 5. Hook: 重试执行节点 (retry_node)
    retried = retry_node(
        workflow,
        thread_id=fork_thread_id,
        values={"file_path": str(csv_file), "task_type": "profile"},
    )
    assert retried["structured_response"]["status"] == "passed"


def test_data_analysis_workflow_full_pipeline(tmp_path: Path) -> None:
    """测试 DataAnalysisWorkflow 类封装、静态确定性拓扑与完整 8 阶段科研分析全流程。"""
    csv_file = tmp_path / "clinical_cohort.csv"
    csv_file.write_text("patient_id,biomarker_a,outcome\nP01,1.5,0\nP02,3.2,1\nP03,4.8,1\nP04,0.9,0\n", encoding="utf-8")

    checkpointer = InMemorySaver()
    workflow = DataAnalysisWorkflow(workspace_root=tmp_path, checkpointer=checkpointer)

    # 验证类封装与属性完整性
    assert workflow.workspace_root == tmp_path.resolve()
    assert workflow.compile() is not None

    # 执行完整的科研数据分析工作流任务 (默认全流程)
    output = workflow.invoke(
        {
            "messages": [HumanMessage(content="请对该临床队列数据开展全流程科研分析并发布报告")],
            "file_path": str(csv_file),
            "research_question": "探究 biomarker_a 对结局 outcome 的预测效应",
        },
        config={"configurable": {"thread_id": "full-pipeline-thread-1"}},
        durability="sync",
    )

    # 1. 验证符合三层输出状态契约
    assert set(output.keys()) <= {"messages", "structured_response"}
    resp = output["structured_response"]
    assert resp["status"] == "passed"

    # 2. 验证全表画像结果
    assert "profile" in resp
    assert resp["profile"]["row_count"] == 4
    assert resp["profile"]["column_count"] == 3

    # 3. 验证真实科学计算脚本与指标生成
    assert (tmp_path / "scripts" / "analyze_data.py").is_file()
    assert (tmp_path / "artifacts" / "analysis_summary.json").is_file()
    assert "analysis" in resp
    assert resp["analysis"]["row_count"] == 4

    # 4. 验证凭据审计通过
    assert "audit" in resp
    assert resp["audit"]["status"] == "passed"

    # 5. 验证包含 7 大段落的中文科研报告原子发布成功
    assert "report" in resp
    assert resp["report"]["status"] == "passed"
    report_file = tmp_path / "artifacts" / "report.md"
    assert report_file.is_file()
    validation_file = tmp_path / "artifacts" / "report_validation.json"
    assert validation_file.is_file()

    report_content = report_file.read_text(encoding="utf-8")
    for heading in ("## 研究问题", "## 数据说明", "## 分析方法", "## 结果", "## 讨论与局限", "## 结论", "## 复现信息"):
        assert heading in report_content


def test_data_analysis_workflow_create_agent_in_node(tmp_path: Path) -> None:
    """测试在工作流节点内利用 create_agent 构建和调用专用子任务智能体。"""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    fake_report = (
        "# 智能体生成报告\n\n"
        "## 研究问题\n智能体澄清的研究问题。\n\n"
        "## 数据说明\n包含 100 例患者数据。\n\n"
        "## 分析方法\n多因素分析 [脚本](../scripts/analyze_data.py)。\n\n"
        "## 结果\n显著相关 [结果](analysis_summary.json)。\n\n"
        "## 讨论与局限\n单中心回顾性队列。\n\n"
        "## 结论\n结论成立。\n\n"
        "## 复现信息\nPython 3.12 虚拟环境。\n"
    )

    fake_model = GenericFakeChatModel(
        messages=iter([
            AIMessage(content="已完成研究设计澄清"),
            AIMessage(content="分析执行摘要已生成"),
            AIMessage(content="凭证审计通过，无数据泄漏"),
            AIMessage(content=fake_report),
        ])
    )

    checkpointer = InMemorySaver()
    workflow = DataAnalysisWorkflow(workspace_root=tmp_path, model=fake_model, checkpointer=checkpointer)

    # 验证 create_node_agent 方法可用
    draft_agent = workflow.create_node_agent("draft_report")
    assert draft_agent is not None

    csv_file = tmp_path / "study_data.csv"
    csv_file.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")

    output = workflow.invoke(
        {
            "messages": [HumanMessage(content="执行智能体辅助分析")],
            "file_path": str(csv_file),
        },
        config={"configurable": {"thread_id": "create-agent-node-test"}},
        durability="sync",
    )
    assert output["structured_response"]["status"] == "passed"
    assert (tmp_path / "artifacts" / "report.md").is_file()


def test_native_guides_loading() -> None:
    """测试原生科研分析指南读取工具与映射函数。"""
    from data_analysis import GUIDE_DESCRIPTIONS, read_analysis_guide

    assert len(GUIDE_DESCRIPTIONS) == 7
    assert "research-analysis" in GUIDE_DESCRIPTIONS
    assert "tabular-data" in GUIDE_DESCRIPTIONS

    # 验证读取原生指南正文
    content = read_analysis_guide("research-analysis")
    assert "Research analysis" in content or "research question" in content.lower()
    assert len(content) > 100

    # 验证 get_node_prompt 支持按指南名称查询
    guide_prompt = get_node_prompt("tabular-data")
    assert len(guide_prompt) > 50

