"""科研数据分析插件（data_analysis）、LangGraph 工作流、三层状态契约、表画像与报告交付测试模块。"""

from __future__ import annotations

import csv
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
    InternalState,
    artifact_paths,
    build_data_analysis_workflow,
    build_data_analysis_workflow_contribution,
    input_schema,
    output_schema,
    profile_table,
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
    # 构造伪造的分析脚本与 JSON 结果产物
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
        "script_sha256": "5bc839d372551a3198bd1a1d9b3d07e29cb0118eb3d42b93ffb307ec3c306be7",
        "exit_code": 0,
        "artifact_sha256": {
            "artifacts/metrics.json": "07c3f309b1fdbce5e933d0eb27429665bc72f10b7efd758c0c97e75ea8d1d8a1",
        },
    }
    # 动态匹配当前文件实际真实哈希
    import hashlib

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


def test_data_analysis_tools_and_guides(tmp_path: Path) -> None:
    """测试 DataAnalysisToolkit 工具读取指南与异常校验。"""
    toolkit = DataAnalysisToolkit(workspace_root=tmp_path)
    tools = {t.name: t for t in toolkit.get_tools()}

    # 1. 测试各指南读取
    read_guide = tools["data_analysis_read_guide"]
    for guide_name in (
        "research-analysis",
        "tabular-data",
        "statistical-analysis",
        "supervised-ml",
        "unsupervised-ml",
        "model-interpretation",
        "reporting-export",
    ):
        content = read_guide.invoke({"name": guide_name})
        assert len(content) > 50

    with pytest.raises((ValueError, Exception)):
        read_guide.invoke({"name": "nonexistent-guide"})
