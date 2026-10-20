import json
import subprocess
import sys

import pytest

from vertical_plugins.common.contracts import BuildServices
from vertical_plugins.common.workflow import WorkflowContext
from vertical_plugins.data_analysis import DataAnalysisWorkflow


def test_saved_analysis_script_recomputes_from_source_with_host_interpreter(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("x,y\n1,2\n2,4\n3,6\n", encoding="utf-8")
    context = WorkflowContext.from_services(BuildServices(tmp_path, python_executable=sys.executable))
    workflow = DataAnalysisWorkflow(context=context)
    result = workflow.analyze_data({
        "validated_input": {"file_path": "source.csv", "analysis_type": "correlation",
                            "target_columns": ["x", "y"]},
        "profile_result": {"columns": [{"name": "x"}, {"name": "y"}], "row_count": 3, "column_count": 2},
    })
    assert result["analysis_result"]["correlations"][0]["pearson_r"] == 1
    assert result["analysis_executions"][0]["exit_code"] == 0

    source.write_text("x,y\n1,6\n2,4\n3,2\n", encoding="utf-8")
    subprocess.run([sys.executable, str(tmp_path / "scripts/analyze_data.py")], check=True)
    summary = json.loads((tmp_path / "artifacts/analysis_summary.json").read_text(encoding="utf-8"))
    assert summary["correlations"][0]["pearson_r"] == -1


def test_failed_analysis_process_does_not_publish_success(tmp_path, monkeypatch):
    from vertical_plugins.data_analysis.workflow import pipeline

    (tmp_path / "source.csv").write_text("x\n1\n", encoding="utf-8")
    workflow = DataAnalysisWorkflow(workspace_root=tmp_path)

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(pipeline.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        workflow.analyze_data({
            "validated_input": {"file_path": "source.csv"},
            "profile_result": {"columns": [{"name": "x"}], "row_count": 1, "column_count": 1},
        })
    assert not (tmp_path / "artifacts/analysis_summary.json").exists()
