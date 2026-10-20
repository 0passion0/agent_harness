"""真实 LangGraph / Deep Agents 执行，只替换外部模型与工具。"""

import asyncio
from contextlib import aclosing
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import MessagesState, StateGraph
from langgraph.types import Command, interrupt

from vertical_harness import HarnessSettings, astream_agent


def test_model_completed_reports_usage_without_stream_double_counting():
    from vertical_harness.engine.streaming import _project

    usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    message = AIMessage(
        content="answer",
        usage_metadata=usage,
        response_metadata={"model_name": "actual-plugin-model"},
    )
    event = {"event": "on_chat_model_end", "data": {"output": message}}
    kind, payload = _project(event, "root", ())
    assert kind == "model.completed"
    assert payload["usage"] == usage
    assert payload["model"] == "actual-plugin-model"
    event = {
        "event": "on_chat_model_stream",
        "data": {"chunk": AIMessageChunk(content="answer", usage_metadata=usage)},
    }
    kind, payload = _project(event, "root", ())
    assert kind == "model.delta"
    assert "usage" not in payload


def test_non_root_chain_error_projects_failed_phase():
    from vertical_harness.engine.streaming import _project

    event = {
        "event": "on_chain_error",
        "run_id": "child-run",
        "name": "child-chain",
        "metadata": {},
        "data": {"error": RuntimeError("private")},
    }
    assert _project(event, "root-run", ()) == ("chain.failed", {})
    event["metadata"] = {"lc_agent_name": "worker"}
    event["name"] = "worker"
    assert _project(event, "root-run", ()) == ("chain.failed", {})


class StreamingModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        message = self._generate(messages).generations[0].message
        if message.tool_calls:
            yield ChatGenerationChunk(
                message=AIMessageChunk(content="", tool_calls=message.tool_calls)
            )
        else:
            for part in [message.content[:2], message.content[2:]]:
                yield ChatGenerationChunk(message=AIMessageChunk(content=part))
                await asyncio.sleep(0)


def _call(name, args, call_id):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _settings(tmp_path):
    return HarnessSettings(
        _env_file=None,
        model="test:model",
        workspace_root=tmp_path,
        store_backend="memory",
        enable_memory=False,
        enable_planning=False,
        enable_skill_gating=False,
    )


def _config():
    return {"run_id": uuid4(), "configurable": {"thread_id": "stream-thread"}}


async def _collect(agent, inputs, config):
    async with aclosing(astream_agent(agent, inputs, config=config)) as events:
        return [event async for event in events]


def test_interrupt_and_resume():

    def approve(state):
        answer = interrupt({"question": "Approve?"})
        return {"messages": [AIMessage(content=str(answer))]}

    graph = StateGraph(MessagesState).add_node("approve", approve)
    graph.set_entry_point("approve")
    agent = graph.compile(checkpointer=InMemorySaver())
    config = _config()

    async def run():
        paused = await _collect(agent, {"messages": []}, config)
        resumed = await _collect(agent, Command(resume="approved"), _config())
        return (paused, resumed)

    paused, resumed = asyncio.run(run())
    assert paused[-1].type == "run.interrupted"
    assert paused[-1].data["interrupts"][0]["value"] == {"question": "Approve?"}
    assert paused[-1].data["interrupts"][0]["id"]
    assert resumed[-1].type == "run.completed"
    assert resumed[-1].data == {"output": "approved"}


def test_failure_is_safe_and_still_raises():

    async def fail(state):
        raise RuntimeError("private exception")

    graph = StateGraph(MessagesState).add_node("fail", fail)
    graph.set_entry_point("fail")
    agent = graph.compile(checkpointer=InMemorySaver())
    events = []

    async def run():
        async with aclosing(astream_agent(agent, {"messages": []}, config=_config())) as stream:
            with pytest.raises(RuntimeError, match="private exception"):
                async for event in stream:
                    events.append(event)

    asyncio.run(run())
    assert events[-1].type == "run.failed"
    assert events[-1].data == {"error_type": "RuntimeError"}
    assert "run.completed" not in [event.type for event in events]
    assert "private" not in events[-1].model_dump_json()


def test_no_checkpointer_and_text_blocks():

    async def answer(state):
        return {
            "messages": [
                AIMessage(
                    content=[
                        {"type": "reasoning", "reasoning": "private reasoning"},
                        {"type": "text", "text": "answer"},
                    ]
                )
            ]
        }

    graph = StateGraph(MessagesState).add_node("answer", answer)
    graph.set_entry_point("answer")
    events = asyncio.run(_collect(graph.compile(), {"messages": []}, _config()))
    assert events[-1].type == "run.completed"
    assert events[-1].data == {"output": "answer"}


@pytest.mark.parametrize("thread_id", [None, "", "   ", 123])
def test_invalid_thread_id_rejected_before_execution(thread_id):

    async def run():
        with pytest.raises(ValueError, match="thread_id"):
            await anext(
                astream_agent(None, None, config={"configurable": {"thread_id": thread_id}})
            )

    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
def test_close_and_cancel_stop_underlying_execution(cancel):

    async def run():
        started, stopped = (asyncio.Event(), asyncio.Event())

        async def wait(state):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        graph = StateGraph(MessagesState).add_node("wait", wait)
        graph.set_entry_point("wait")
        stream = astream_agent(
            graph.compile(checkpointer=InMemorySaver()), {"messages": []}, config=_config()
        )
        events = []

        async def consume():
            async with aclosing(stream):
                async for event in stream:
                    events.append(event)
                    if not cancel:
                        await asyncio.wait_for(started.wait(), 3)
                        break

        task = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), 3)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await task
        assert stopped.is_set()
        assert not any(event.type in {"run.completed", "run.failed"} for event in events)

    asyncio.run(run())
