"""评测使用真实 LangSmith 本地 runner；边界上的模型/网络以 fake 替换。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import requests
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langmem import create_prompt_optimizer
from langsmith import Client
from langsmith.run_helpers import get_tracing_context

from vertical_harness import AgentComposer, BuildServices, HarnessSettings
from vertical_harness.engine.plugin_loader import discover_plugins
from vertical_harness.evaluation import (
    EvaluationSettings,
    EvaluationTarget,
    RegressionCase,
    cli,
    evaluate_offline,
    load_cases,
    offline,
    optimize_prompt,
    scoring,
)
from vertical_harness.evaluation.datasets import ensure_disjoint
from vertical_harness.evaluation.offline import compare_reports
from vertical_harness.evaluation.scoring import (
    deterministic_scores,
    input_payload,
    normalize_outputs,
)
from vertical_harness.evaluation.targets import make_target


@pytest.fixture
def local_client(monkeypatch):
    request = Mock(side_effect=AssertionError("Local evaluation must not contact LangSmith"))
    monkeypatch.setattr(requests.Session, "request", request)
    client = Client(api_key="test", auto_batch_tracing=False, info={"version": "0.16.0"})
    yield client
    client.close()
    request.assert_not_called()


def settings(tmp_path, **kwargs):
    return HarnessSettings(
        _env_file=None,
        model="openai:va-model",
        api_key="private-va-key",
        base_url="https://va.invalid/v1",
        workspace_root=tmp_path,
        enabled_plugins=["research.citations"],
        **kwargs,
    )


def citation_case(prompt=None):
    return RegressionCase(
        inputs={
            "prompt": prompt
            or json.dumps({"manuscript_text": "Source [1] and [3].", "bibliography": ["A", "B"]})
        },
        outputs={"structured_response": {"status": "issues"}},
    )


def test_real_local_runner_executes_plugin_workflow_without_network(tmp_path, local_client):
    report = asyncio.run(
        evaluate_offline(
            settings(tmp_path),
            EvaluationSettings(_env_file=None),
            local_client,
            [citation_case()],
            target=EvaluationTarget("workflow:citation_audit"),
            judge=False,
        )
    )
    assert report["scores"] == {"completed": 1, "reference_match": 1}
    assert report["errors"] == report["failures"] == 0
    assert report["usage"]["complete"]
    assert report["usage"]["model_calls"] == report["usage"]["total_tokens"] == 0
    assert report["usage"]["duration_ms"] >= 0
    assert not list(tmp_path.iterdir())


def test_target_and_judge_failures_are_reported(tmp_path, local_client, monkeypatch):
    async def predict(inputs):
        if inputs["prompt"] == "bad":
            raise RuntimeError("broken target")
        return {"output": "ok"}

    async def judge(**kwargs):
        raise RuntimeError("broken judge")

    monkeypatch.setattr(offline, "make_target", lambda *args, **kwargs: predict)
    monkeypatch.setattr(offline, "make_judge", lambda *args: judge)
    report = asyncio.run(
        evaluate_offline(
            settings(tmp_path),
            EvaluationSettings(_env_file=None),
            local_client,
            [RegressionCase(inputs={"prompt": "bad"}), RegressionCase(inputs={"prompt": "good"})],
        )
    )
    assert report["errors"] >= 2
    assert report["count"] == 2
    assert report["failures"] >= 1


def test_judge_and_optimizer_use_va_connection(tmp_path, local_client, monkeypatch):
    config = settings(tmp_path)
    evaluation = EvaluationSettings(
        _env_file=None, eval_model="openai:va-judge", optimizer_model="openai:va-optimizer"
    )
    models = []

    def init_model(settings, model):
        models.append((settings.model_init_kwargs, model))
        return model

    monkeypatch.setattr(scoring, "init_configured_model", init_model)
    factory = Mock(return_value="judge")
    monkeypatch.setattr(scoring, "create_async_llm_as_judge", factory)
    assert scoring.make_judge(config, evaluation) == "judge"
    assert factory.call_args.kwargs["judge"] == "openai:va-judge"
    monkeypatch.setattr(offline, "init_configured_model", init_model)
    optimizer = Mock(ainvoke=AsyncMock(return_value="Improved evidence-first prompt"))
    monkeypatch.setattr(offline, "create_prompt_optimizer", lambda *args, **kwargs: optimizer)

    async def fake_evaluate(settings, evaluation, client, cases, **kwargs):
        return {
            "rows": [
                {
                    "example_id": str(cases[0].to_example().id),
                    "inputs": cases[0].inputs,
                    "outputs": {"output": "answer"},
                    "feedback": [],
                }
            ],
            "count": len(cases),
            "errors": 0,
            "scores": {"quality": 1 if kwargs.get("prompt") else 0},
        }

    monkeypatch.setattr(offline, "evaluate_offline", fake_evaluate)
    directory = tmp_path / "candidate"
    report = asyncio.run(
        optimize_prompt(
            config,
            evaluation,
            local_client,
            [RegressionCase(inputs={"prompt": "training"})],
            [RegressionCase(inputs={"prompt": "validation"})],
            output_dir=directory,
        )
    )
    assert models == [
        (config.model_init_kwargs, "openai:va-judge"),
        (config.model_init_kwargs, "openai:va-optimizer"),
    ]
    assert report["comparison"]["recommendation"] == "review_candidate"
    assert report["comparison"]["production_changed"] is False
    assert (directory / "candidate_prompt.txt").read_text() == "Improved evidence-first prompt"
    trajectory = optimizer.ainvoke.call_args.args[0]["trajectories"]
    assert "training" in trajectory and "validation" not in trajectory
    assert config == settings(tmp_path)


def test_overrides_only_change_selected_plugin_instruction(tmp_path):
    config = settings(tmp_path)
    composer = AgentComposer(
        settings=config, prompt_overrides={"research.citations": "candidate plugin instruction"}
    )
    composer.register_plugins(discover_plugins(config.enabled_plugins), BuildServices(tmp_path))
    assert "[research.citations] candidate plugin instruction" in composer.system_prompt
    assert "workflow supervisor" in composer.system_prompt
    assert EvaluationTarget("plugin:research.citations").baseline_prompt(config)
    with pytest.raises(ValueError, match="no prompt"):
        EvaluationTarget("workflow:citation_audit").baseline_prompt(config)


def test_candidate_cannot_be_recommended_on_regressions_or_missing_metrics():
    baseline = {"count": 2, "errors": 0, "scores": {"quality": 0.5, "completed": 1}}
    candidate = {"count": 2, "errors": 0, "scores": {"quality": 1, "completed": 0.5}}
    assert compare_reports(baseline, candidate)["recommendation"] == "keep_baseline"
    candidate["scores"] = {"quality": 1}
    assert compare_reports(baseline, candidate)["recommendation"] == "keep_baseline"
    candidate["scores"] = {"quality": 1, "completed": 1}
    candidate["errors"] = 1
    assert compare_reports(baseline, candidate)["recommendation"] == "keep_baseline"


@pytest.mark.parametrize("complete,quality,expected", [
    (True, 1, "review_candidate"),
    (False, 1, "keep_baseline"),
    (True, 0, "keep_baseline"),
])
def test_lower_token_usage_requires_complete_accounting_and_no_quality_regression(complete, quality, expected):
    row = {"example_id": "one", "feedback": [{"key": "quality", "score": 1}]}
    usage = {"model_calls": 2, "input_tokens": 80, "output_tokens": 20, "total_tokens": 100, "duration_ms": 10, "complete": True}
    baseline = {"count": 1, "errors": 0, "scores": {"quality": 1}, "rows": [row], "usage": usage}
    candidate = {**baseline, "scores": {"quality": quality}, "usage": {**usage, "total_tokens": 60, "input_tokens": 40, "complete": complete}}
    result = compare_reports(baseline, candidate)
    assert result["recommendation"] == expected
    assert (result["usage_deltas"] is not None) == complete


def test_judge_does_not_receive_execution_cost_as_answer_evidence():
    assert scoring.judge_payload({"output": "answer", "usage": {"total_tokens": 123}}) == '{"output": "answer"}'


def test_offline_report_accounts_for_model_usage_without_network(tmp_path, local_client, monkeypatch):
    from test_supervisor_workflows_and_time_travel import ToolCallingFakeModel
    from vertical_harness.engine import compose

    model = ToolCallingFakeModel(responses=[AIMessage(content="answer", usage_metadata={
        "input_tokens": 80, "output_tokens": 20, "total_tokens": 100,
        "input_token_details": {"cache_read": 40},
    })])
    monkeypatch.setattr(compose, "init_configured_model", lambda *_: model)
    report = asyncio.run(evaluate_offline(
        settings(tmp_path), EvaluationSettings(_env_file=None), local_client,
        [RegressionCase(inputs={"prompt": "task"})], judge=False,
    ))
    assert report["usage"]["complete"]
    assert report["usage"]["model_calls"] == 1
    assert report["usage"]["total_tokens"] == 100
    assert report["rows"][0]["outputs"]["usage"]["input_token_details"]["cache_read"] == 40


def test_individual_regression_is_not_hidden_by_better_average():
    baseline = {
        "count": 2,
        "errors": 0,
        "scores": {"quality": 0.5},
        "rows": [
            {"example_id": "one", "feedback": [{"key": "quality", "score": 1}]},
            {"example_id": "two", "feedback": [{"key": "quality", "score": 0}]},
        ],
    }
    candidate = {
        "count": 2,
        "errors": 0,
        "scores": {"quality": 0.75},
        "rows": [
            {"example_id": "one", "feedback": [{"key": "quality", "score": 0.5}]},
            {"example_id": "two", "feedback": [{"key": "quality", "score": 1}]},
        ],
    }
    result = compare_reports(baseline, candidate)
    assert result["recommendation"] == "keep_baseline"
    assert result["regressions"] == [{"example_id": "one", "key": "quality"}]


def test_actual_openevals_and_langmem_structured_model_contract(
    tmp_path, monkeypatch, local_client
):
    class StructuredModel(FakeListChatModel):
        def with_structured_output(self, schema, **kwargs):
            return RunnableLambda(
                lambda value: {"score": 1, "reasoning": "Supported", "new_prompt": "candidate"}
            )

    model = StructuredModel(responses=["unused"])
    monkeypatch.setattr(scoring, "init_configured_model", lambda *args: model)
    judge = scoring.make_judge(settings(tmp_path), EvaluationSettings(_env_file=None))
    result = asyncio.run(judge(inputs="task", outputs="answer", reference_outputs="reference"))
    assert result["score"] == 1
    optimizer = create_prompt_optimizer(model, kind="prompt_memory")
    from langsmith import tracing_context

    async def optimize():
        with tracing_context(enabled=False):
            return await optimizer.ainvoke(
                {
                    "prompt": {"name": "test", "prompt": "original"},
                    "trajectories": "training results",
                }
            )

    assert asyncio.run(optimize()) == "candidate"


def test_text_reference_needs_semantic_judge(tmp_path, local_client):
    with pytest.raises(ValueError, match="require a judge"):
        asyncio.run(
            evaluate_offline(
                settings(tmp_path),
                EvaluationSettings(_env_file=None),
                local_client,
                [RegressionCase(inputs={"prompt": "task"}, outputs={"output": "answer"})],
                judge=False,
            )
        )


def test_dataset_validation_and_held_out_leakage(tmp_path):
    file = tmp_path / "cases.jsonl"
    file.write_text(citation_case().model_dump_json() + "\n", encoding="utf-8")
    assert load_cases(path=file)[0].outputs["structured_response"]["status"] == "issues"
    with pytest.raises(ValueError, match="overlap"):
        ensure_disjoint(
            [RegressionCase(inputs={"prompt": "same"})],
            [RegressionCase(inputs={"messages": [{"role": "user", "content": "same"}]})],
        )
    with pytest.raises(ValueError, match="only"):
        RegressionCase(
            inputs={"messages": [{"role": "user", "content": "x"}], "internal_state": "injected"}
        )
    file.write_text("")
    with pytest.raises(ValueError, match="empty"):
        load_cases(path=file)


def test_serialized_trace_messages_and_required_tool_checks():
    payload = input_payload(
        {
            "messages": [
                {"lc": 1, "type": "constructor", "kwargs": {"type": "human", "content": "task"}}
            ]
        }
    )
    assert payload == {"messages": [{"role": "human", "content": "task"}]}
    output = normalize_outputs(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "task", "args": {"subagent_type": "citation_audit"}, "id": "1"}
                    ],
                ),
                AIMessage(content="done"),
            ]
        }
    )
    assert (
        deterministic_scores(output, {"required_tools": ["task:citation_audit"]})[-1]["score"] == 1
    )
    assert (
        deterministic_scores(output, {"structured_response": {"status": "wrong"}})[-1]["score"] == 0
    )


def test_main_and_plugin_targets_isolate_storage_and_memory(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    from vertical_harness.evaluation import targets

    captured = []

    @asynccontextmanager
    async def open_agent(config, **kwargs):
        captured.append((config, kwargs))
        assert not config.enable_memory
        assert config.store_backend == "memory"
        assert config.resolved_workspace_root != tmp_path
        assert get_tracing_context()["enabled"] is False
        yield SimpleNamespace(
            ainvoke=AsyncMock(return_value={"messages": [AIMessage(content="done")]})
        )

    monkeypatch.setattr(targets, "aopen_agent", open_agent)
    from langsmith import tracing_context

    async def invoke():
        with tracing_context(enabled=False):
            return await make_target(
                settings(tmp_path),
                EvaluationTarget("plugin:research.citations"),
                prompt="candidate",
            )({"prompt": "task"})

    assert asyncio.run(invoke())["output"] == "done"
    assert captured[0][1]["prompt_overrides"] == {"research.citations": "candidate"}
    assert not captured[0][0].resolved_workspace_root.exists()


def test_cli_writes_report_and_fails_regression_checks(tmp_path, monkeypatch, local_client, capsys):
    file, report = tmp_path / "cases.jsonl", tmp_path / "report.json"
    file.write_text(citation_case().model_dump_json())
    monkeypatch.setattr(cli, "HarnessSettings", lambda: settings(tmp_path))
    monkeypatch.setattr(cli.LangSmithSettings, "create_client", lambda self, **kwargs: local_client)

    async def evaluation(*args, **kwargs):
        return {"errors": 0, "failures": 1, "rows": [], "count": 1}

    monkeypatch.setattr(cli, "evaluate_offline", evaluation)
    with pytest.raises(SystemExit) as error:
        cli.main(["run", "--data", str(file), "--report", str(report), "--no-judge"])
    assert error.value.code == 1
    assert json.loads(report.read_text())["failures"] == 1
    assert "checks failed" in capsys.readouterr().err


def test_historical_tool_calls_and_user_messages_cannot_satisfy_a_new_run():
    historical = AIMessage(
        content="",
        tool_calls=[
            {"name": "task", "args": {"subagent_type": "citation_audit"}, "id": "old-call"}
        ],
    )
    output = normalize_outputs(
        {"messages": [historical, AIMessage(content="I did not call a tool this turn")]},
        inputs={"messages": [historical]},
    )
    assert (
        deterministic_scores(output, {"required_tools": ["task:citation_audit"]})[-1]["score"] == 0
    )
    unfinished = normalize_outputs({"messages": [{"role": "user", "content": "unanswered"}]})
    assert deterministic_scores(unfinished, None)[0]["score"] == 0
