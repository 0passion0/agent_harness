"""反馈、回归集和在线 worker 使用无网络 SDK 边界测试。"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from langsmith.utils import LangSmithNotFoundError

from vertical_harness import HarnessSettings
from vertical_harness.evaluation import (
    EvaluationSettings,
    ReferenceAnswer,
    UserFeedback,
    evaluate_online_once,
    online,
    promote_feedback,
    submit_feedback,
    sync_feedback_dataset,
)
from vertical_harness.evaluation.datasets import RegressionCase, experiment_examples
from vertical_harness.evaluation.feedback import USER_FEEDBACK_KEY, feedback_case
from vertical_harness.evaluation.online import sampled


def run(**changes):
    record = {
        "id": str(uuid4()),
        "name": "vertical-agent",
        "project_id": str(uuid4()),
        "start_time": datetime.now(UTC),
        "end_time": datetime.now(UTC),
        "inputs": {"messages": [{"role": "user", "content": "private task"}]},
        "outputs": {"messages": [{"type": "ai", "content": "old wrong answer"}]},
        "error": None,
        "feedback_stats": {},
    }
    return SimpleNamespace(**(record | changes))


def backend(runs):
    project_id = runs[0].project_id
    feedbacks, examples = {}, {}
    client = Mock()
    client.read_project.return_value = SimpleNamespace(id=project_id)
    client.read_dataset.return_value = SimpleNamespace(id=uuid4())

    async def query(**kwargs):
        assert kwargs["project_ids"] == [project_id]
        for record in runs:
            yield record

    client.runs.query.side_effect = query

    async def retrieve(run_id, *, project_id):
        return next(
            record for record in runs if record.id == run_id and record.project_id == project_id
        )

    client.runs.retrieve = AsyncMock(side_effect=retrieve)

    def create_feedback(**kwargs):
        record = SimpleNamespace(
            id=kwargs.get("feedback_id") or uuid4(),
            run_id=kwargs["run_id"],
            session_id=kwargs.get("session_id"),
            key=kwargs["key"],
            score=kwargs.get("score"),
            correction=kwargs.get("correction"),
            comment=kwargs.get("comment"),
        )
        feedbacks[str(record.id)] = record
        return record

    client.create_feedback.side_effect = create_feedback
    client.read_feedback.side_effect = lambda key: feedbacks[str(key)]

    def list_feedback(*, run_ids, feedback_key, **kwargs):
        return iter(
            item
            for item in feedbacks.values()
            if str(item.run_id) in {str(key) for key in run_ids} and item.key in feedback_key
        )

    client.list_feedback.side_effect = list_feedback

    def read_example(key):
        if str(key) not in examples:
            raise LangSmithNotFoundError("missing")
        return examples[str(key)]

    client.read_example.side_effect = read_example

    def create_example(**kwargs):
        item = SimpleNamespace(
            id=kwargs["example_id"],
            **{key: value for key, value in kwargs.items() if key != "example_id"},
        )
        examples[str(item.id)] = item
        return item

    client.create_example.side_effect = create_example
    return client, feedbacks, examples


def settings(tmp_path):
    return HarnessSettings(
        _env_file=None,
        model="openai:va-main",
        api_key="va-private",
        base_url="https://va.invalid",
        workspace_root=tmp_path,
        enabled_plugins=[],
    )


def test_feedback_with_correction_builds_idempotent_replayable_case():
    record = run()
    client, _, examples = backend([record])
    config = EvaluationSettings(
        _env_file=None, enable_feedback_regression=True, regression_dataset="regression"
    )
    feedback = UserFeedback(
        run_id=record.id,
        score=0,
        comment="Should say corrected",
        correction=ReferenceAnswer(output="corrected answer"),
    )
    result = asyncio.run(submit_feedback(client, feedback, config, project="production"))
    repeated = asyncio.run(
        promote_feedback(client, result["feedback_id"], "regression", project="production")
    )
    assert result["example_id"] == repeated
    assert len(examples) == 1
    example = examples[result["example_id"]]
    assert example.outputs == {"output": "corrected answer"}
    assert "old wrong answer" not in str(example.inputs) + str(example.outputs)
    assert example.metadata["source_run_id"] == record.id
    assert example.metadata["feedback_id"] == result["feedback_id"]
    assert client.create_example.call_count == 1
    assert client.create_feedback.call_args.kwargs["session_id"] == record.project_id


def test_rating_without_correction_does_not_create_gold_reference():
    record = run()
    client, feedbacks, examples = backend([record])
    config = EvaluationSettings(
        _env_file=None, enable_feedback_regression=True, regression_dataset="regression"
    )
    result = asyncio.run(
        submit_feedback(
            client,
            UserFeedback(run_id=record.id, score=1, comment="nice"),
            config,
            project="production",
        )
    )
    assert "example_id" not in result and not examples
    with pytest.raises(ValueError, match="explicit correction"):
        feedback_case(feedbacks[result["feedback_id"]], record)


@pytest.mark.parametrize(
    "changes", [{"score": -0.1}, {"score": 1.1}, {"run_id": "not-a-uuid"}, {"score": float("nan")}]
)
def test_feedback_validates_external_input(changes):
    with pytest.raises(ValueError):
        UserFeedback.model_validate({"run_id": uuid4(), "score": 0} | changes)


def test_reference_and_config_require_explicit_answers_and_dataset():
    for reference in (
        {},
        {"output": " "},
        {"required_tools": ["task"]},
        {"output": "ok", "extra": "injected"},
    ):
        with pytest.raises(ValueError):
            ReferenceAnswer.model_validate(reference)
    with pytest.raises(ValueError):
        EvaluationSettings(_env_file=None, enable_feedback_regression=True)


def test_feedback_cannot_be_promoted_across_projects():
    record = run()
    client, feedbacks, _ = backend([record])
    feedback_id = uuid4()
    feedbacks[str(feedback_id)] = SimpleNamespace(
        id=feedback_id, run_id=record.id, session_id=uuid4()
    )
    with pytest.raises(ValueError, match="another tracing project"):
        asyncio.run(promote_feedback(client, feedback_id, "regression", project="production"))


def test_online_sampling_and_deduplication(tmp_path, monkeypatch):
    first, second, pending = run(), run(), run(end_time=None)
    second.project_id = pending.project_id = first.project_id
    client, _, _ = backend([first, second, pending])
    judge = AsyncMock(return_value={"key": "quality", "score": 1, "comment": "supported"})
    factory = Mock(return_value=judge)
    monkeypatch.setattr(online, "make_judge", factory)
    config = EvaluationSettings(
        _env_file=None, enable_online_evaluation=True, online_eval_sample_rate=1
    )
    result = asyncio.run(
        evaluate_online_once(settings(tmp_path), config, client, project="production")
    )
    assert result == {"evaluated": 2, "skipped": 0, "errors": 0}
    assert judge.await_count == 2
    again = asyncio.run(
        evaluate_online_once(settings(tmp_path), config, client, project="production")
    )
    assert again == {"evaluated": 0, "skipped": 2, "errors": 0}
    assert judge.await_count == 2
    assert client.runs.query.call_args.kwargs["filter"] == 'eq(name,"vertical-agent")'
    assert sampled(first.id, 1) and not sampled(first.id, 0)
    assert sampled(first.id, 0.1) == sampled(first.id, 0.1)
    assert client.create_feedback.call_args.kwargs["feedback_source_type"] == "model"


def test_failed_and_interrupted_online_runs_skip_judge(tmp_path, monkeypatch):
    failed, interrupted = run(error="error"), run(outputs={"__interrupt__": ["approval required"]})
    interrupted.project_id = failed.project_id
    client, _, _ = backend([failed, interrupted])
    factory = Mock(side_effect=AssertionError("No judge needed"))
    monkeypatch.setattr(online, "make_judge", factory)
    config = EvaluationSettings(
        _env_file=None, enable_online_evaluation=True, online_eval_sample_rate=1
    )
    result = asyncio.run(
        evaluate_online_once(settings(tmp_path), config, client, project="production")
    )
    assert result["evaluated"] == 2
    assert all(call.kwargs["score"] == 0 for call in client.create_feedback.call_args_list)
    factory.assert_not_called()


def test_online_one_judge_error_does_not_skip_remaining_runs(tmp_path, monkeypatch):
    first, second = run(), run()
    second.project_id = first.project_id
    client, _, _ = backend([first, second])
    monkeypatch.setattr(
        online,
        "make_judge",
        lambda *args: AsyncMock(side_effect=[RuntimeError("judge failed"), {"score": 1}]),
    )
    config = EvaluationSettings(
        _env_file=None, enable_online_evaluation=True, online_eval_sample_rate=1
    )
    result = asyncio.run(
        evaluate_online_once(settings(tmp_path), config, client, project="production")
    )
    assert result == {"evaluated": 1, "skipped": 0, "errors": 1}


def test_disabled_worker_makes_no_network_requests(tmp_path):
    client = Mock()
    with pytest.raises(ValueError, match="Enable VA_ENABLE_ONLINE"):
        asyncio.run(
            evaluate_online_once(
                settings(tmp_path), EvaluationSettings(_env_file=None), client, project="production"
            )
        )
    assert not client.mock_calls


def test_sync_feedback_uses_modification_time_and_excludes_uncorrected_records():
    record = run()
    client, _, examples = backend([record])
    client.create_feedback(
        run_id=record.id, key=USER_FEEDBACK_KEY, score=0, correction={"output": "new"}
    )
    client.create_feedback(run_id=record.id, key=USER_FEEDBACK_KEY, score=0)
    result = asyncio.run(
        sync_feedback_dataset(
            client, project="production", dataset="regression", since=datetime.now(UTC)
        )
    )
    assert result == {"promoted": 1, "skipped": 1, "errors": 0}
    assert len(examples) == 1
    assert "min_feedback_modified_at" in client.runs.query.call_args.kwargs


def test_uploaded_experiments_share_an_immutable_dataset_snapshot():
    client, _, examples = backend([run()])
    cases = [RegressionCase(inputs={"prompt": "task"}, outputs={"output": "reference"})]
    first = asyncio.run(experiment_examples(client, cases, True))
    again = asyncio.run(experiment_examples(client, cases, True))
    assert first[0].id == again[0].id
    assert first[0].dataset_id != "00000000-0000-0000-0000-000000000000"
    assert len(examples) == 1
    assert client.create_example.call_count == 1
    assert client.read_dataset.call_args.kwargs["dataset_name"].startswith("va-eval-")
    first[0].outputs = {"output": "changed after baseline"}
    with pytest.raises(ValueError, match="snapshot contents changed"):
        asyncio.run(experiment_examples(client, cases, True))
