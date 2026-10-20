import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from evaluate_table_demos import _check_result, _interrupt_answer, _turn_prompts


def test_demo_one_prompts_run_each_original_analysis_then_audit_without_refitting():
    expected = [
        "## 主分析",
        "## 补充分析一：30 天死亡",
        "## 补充分析二：炎症指标变化",
        "## 补充分析三：住院时长",
        "## 补充分析四：抗菌药物策略的观察性比较",
        "## 补充分析五：入院生物标志物关系",
    ]
    requirements = [
        "主模型要求", "HR、95%置信区间和 p 值", "中位数、配对差值、检验统计量和 p 值",
        "说明系数的解释尺度", "不能仅根据组间差异得出疗效或因果结论",
        "Spearman 相关，相关性不代表因果关系",
    ]
    original = "任务背景\n\n" + "\n\n".join(
        f"{heading}\n{requirement}" for heading, requirement in zip(expected, requirements, strict=True)
    ) + "\n\n## 数据处理要求\n不得补造数据。"
    prompts = _turn_prompts(original, 1)
    assert len(prompts) == 7
    for prompt, heading in zip(prompts, expected, strict=False):
        assert heading in prompt
        assert "本轮单独确认并执行这一项分析" in prompt
    assert all("数据处理要求" in prompt for prompt in prompts[:1])
    assert "HR、95%置信区间和 p 值" in prompts[1]
    assert "中位数、配对差值、检验统计量和 p 值" in prompts[2]
    assert "说明系数的解释尺度" in prompts[3]
    assert "不能仅根据组间差异得出疗效或因果结论" in prompts[4]
    assert "Spearman 相关" in prompts[5] and "相关性不代表因果关系" in prompts[5]
    assert "不要运行新的分析、重新拟合模型" in prompts[6]


def test_evaluator_requires_desktop_utf8_process_mode(monkeypatch, capsys):
    from types import SimpleNamespace

    import evaluate_table_demos
    import pytest

    monkeypatch.setattr(sys, "argv", ["evaluate_table_demos", "--demo-root", ".", "--output-dir", "unused", "--phase", "candidate", "--demos", "18"])
    monkeypatch.setattr(evaluate_table_demos.sys, "flags", SimpleNamespace(utf8_mode=0))
    with pytest.raises(SystemExit, match="2"):
        evaluate_table_demos.main()
    assert "Python -X utf8" in capsys.readouterr().err


def test_evaluator_exit_status_reports_failed_conversations(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import evaluate_table_demos

    async def scenario():
        for index, checks in enumerate([
            {"turn_errors": [], "model_budget_exhausted": False},
            {"turn_errors": [{"type": "TimeoutError"}], "model_budget_exhausted": False},
            {"turn_errors": [], "model_budget_exhausted": True},
        ]):
            async def run_demo(*_args, checks=checks):
                return {"checks": checks}

            monkeypatch.setattr(evaluate_table_demos, "_run_demo", run_demo)
            args = SimpleNamespace(demo_root=tmp_path, output_dir=tmp_path / str(index),
                                   demos=[1], phase="candidate", concurrency=1,
                                   turn_timeout=300)
            assert await evaluate_table_demos._main(args) == (0 if index == 0 else 1)

    asyncio.run(scenario())


def test_other_demo_followups_keep_original_scope():
    prompts = _turn_prompts("完整原始任务", 2)
    assert prompts[0] == "完整原始任务"
    assert len(prompts) == 3
    assert all("原需求" in prompt and "仅完成" not in prompt for prompt in prompts[1:])


def test_evaluator_rejects_batch_before_creating_output(tmp_path):
    import asyncio
    from types import SimpleNamespace

    import evaluate_table_demos
    import pytest

    output = tmp_path / "not-created"
    args = SimpleNamespace(demos=[1, 2], concurrency=1, output_dir=output)
    with pytest.raises(ValueError, match="exactly one demo"):
        asyncio.run(evaluate_table_demos._main(args))
    assert not output.exists()


def test_conversation_budget_counts_all_turns_and_summary_calls():
    from uuid import uuid4

    import pytest
    from evaluate_table_demos import ConversationBudget
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    budget = ConversationBudget()
    result = LLMResult(generations=[[ChatGeneration(message=AIMessage(content="summary", usage_metadata={
        "input_tokens": 12_000, "output_tokens": 1000, "total_tokens": 13_000,
    }))]])
    for _ in range(24):
        budget.on_chat_model_start({}, [[HumanMessage(content="next turn or summary")]], run_id=uuid4())
        budget.on_llm_end(result)
    with pytest.raises(RuntimeError, match="budget exhausted"):
        budget.on_chat_model_start({}, [[HumanMessage(content="cannot spend more")]])
    assert budget.model_calls == 24
    assert budget.total_tokens == 312_000
    assert len(budget.request_sizes) == 24


def test_conversation_budget_stops_before_the_model_runs():
    import pytest
    from evaluate_table_demos import ConversationBudget
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    class MustNotRun(FakeListChatModel):
        def _call(self, *args, **kwargs):
            raise AssertionError("budget must stop before execution")

    budget = ConversationBudget()
    budget.total_tokens = 300_000
    with pytest.raises(RuntimeError, match="budget exhausted"):
        MustNotRun(responses=["unused"]).invoke("Next turn", config={"callbacks": [budget]})
    assert budget.model_calls == 0


def test_native_hitl_interrupt_resumes_with_one_decision_per_action():
    from typing import TypedDict

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt

    class State(TypedDict, total=False):
        approved: object

    payload = {"action_requests": [{"name": "write_file", "args": {}}, {"name": "execute_python_file", "args": {}}],
               "review_configs": [{"action_name": "write_file"}, {"action_name": "execute_python_file"}]}
    graph = StateGraph(State)
    graph.add_node("review", lambda _state: {"approved": interrupt(payload)})
    graph.add_edge(START, "review")
    graph.add_edge("review", END)
    agent = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "native-hitl-resume-test"}}
    interrupted = agent.invoke({}, config)
    request = interrupted["__interrupt__"][0].value
    answer = _interrupt_answer(request, 1)
    assert answer == {"decisions": [{"type": "approve"}, {"type": "approve"}]}
    resumed = agent.invoke(Command(resume=answer), config)
    assert resumed["approved"] == answer


def test_plugin_authorization_interrupt_returns_gateway_contract_dict():
    assert _interrupt_answer({"kind": "plugin_authorization", "request_id": "request-1"}, 1) == {
        "request_id": "request-1", "approved": True
    }


def test_runner_stream_config_matches_desktop_and_uses_graph_recursion_limit():
    import asyncio
    from typing import TypedDict

    from evaluate_table_demos import _desktop_stream_config
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph

    from vertical_harness.engine.streaming import astream_agent
    from vertical_plugins.common.contracts import RunContext

    class State(TypedDict):
        count: int

    graph = StateGraph(State)
    graph.add_node("step", lambda state: {"count": state["count"] + 1})
    graph.add_conditional_edges("step", lambda state: END if state["count"] == 30 else "step")
    graph.add_edge(START, "step")
    agent = graph.compile(checkpointer=InMemorySaver()).with_config({"recursion_limit": 40})
    context = RunContext(tenant_id="test-tenant", user_id="test-user")
    callbacks = []
    config = _desktop_stream_config(agent, "stream-config-test", "turn-config-test", context,
                                   ["data_analysis"], [], callbacks)
    assert config["configurable"] == {"thread_id": "stream-config-test", "checkpoint_ns": ""}
    assert config["recursion_limit"] == agent.config["recursion_limit"] == 40
    assert config["run_id"] == config["metadata"]["desktop_run_id"] == "turn-config-test"
    assert config["metadata"]["desktop_tenant_id"] == context.tenant_id
    assert config["metadata"]["desktop_user_id"] == context.user_id
    assert config["callbacks"] is callbacks

    async def run_stream():
        events = [event async for event in astream_agent(agent, {"count": 0}, config=config,
                                                        context=context, timeout_seconds=5)]
        assert any(event.type == "run.completed" for event in events)
        assert not any(event.type == "run.failed" for event in events)
        assert (await agent.aget_state(config)).values["count"] == 30

    asyncio.run(run_stream())


def test_checks_use_tool_evidence_from_any_turn_and_hide_private_runtime(tmp_path: Path):
    (tmp_path / ".private-runtime").mkdir()
    (tmp_path / ".private-runtime" / "checkpoints.sqlite").write_bytes(b"database")
    (tmp_path / "report.xlsx").write_bytes(b"artifact")
    result = _check_result(
        "已经完成分析，样本共 21 行，平均值为 5.2。这里给出依据和限制。",
        [{"type": "tool.failed", "data": {}}],
        [{"name": "execute_python_file", "phase": "completed"}],
        tmp_path,
    )
    assert result["artifact_names"] == ["report.xlsx"]
    assert result["tool_evidence"] is True
    assert result["execution_errors"]
    assert result["needs_review"] is True


def test_model_limit_message_is_not_a_successful_final_answer(tmp_path: Path):
    (tmp_path / "report.md").write_text("partial results")
    result = _check_result(
        "Model call limits exceeded: run limit (16/16)", [],
        [{"name": "execute_python_file", "phase": "completed"}], tmp_path,
    )
    assert result["model_budget_exhausted"]
    assert result["needs_review"]
    assert result["scope_review"] == "pending_human_review"
