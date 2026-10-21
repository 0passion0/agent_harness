import asyncio
import json
from contextlib import nullcontext
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest
from langchain_core.callbacks import BaseCallbackHandler, CallbackManager
from langchain_core.exceptions import (
    ModelAPIError,
    ModelConnectionError,
    ModelInvalidRequestError,
    ModelTimeoutError,
)
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphInterrupt
from openai import APIStatusError, APITimeoutError

from vertical_harness.middleware.model_output_guard import ModelOutputGuard
from vertical_harness.middleware.model_retry import OpenCodeModelRetryMiddleware


def model_request():
    return ModelRequest(model=FakeListChatModel(responses=["ok"], metadata={"model": "retained"}), messages=[])


def emit_then_fail(request):
    manager = CallbackManager.configure()
    manager.on_llm_start({"name": "test"}, ["input"])[0].on_llm_new_token("partial")
    raise status_error(503)


def status_error(status, *, headers=None, code=None, message="provider failure"):
    response = httpx.Response(status, headers=headers, request=httpx.Request("POST", "https://model.test"))
    return APIStatusError(message, response=response, body={"code": code})


@pytest.mark.parametrize("execution", ["sync", "async"])
def test_model_retries_are_bounded_and_keep_request(monkeypatch, execution):
    delays = []
    monkeypatch.setattr("vertical_harness.middleware.model_retry.random.uniform", lambda *_: 1)
    monkeypatch.setattr("vertical_harness.middleware.model_retry.time.sleep", delays.append)
    asleep = AsyncMock(side_effect=delays.append)
    monkeypatch.setattr("vertical_harness.middleware.model_retry.asyncio.sleep", asleep)
    error = status_error(503)
    handler = Mock(side_effect=error) if execution == "sync" else AsyncMock(side_effect=error)
    request = model_request()
    middleware = OpenCodeModelRetryMiddleware()
    with pytest.raises(APIStatusError) as raised:
        if execution == "sync":
            middleware.wrap_model_call(request, handler)
        else:
            asyncio.run(middleware.awrap_model_call(request, handler))
    assert raised.value is error
    assert handler.call_count == 11
    guarded = handler.call_args.args[0]
    assert guarded is request
    assert all(call.args == (guarded,) for call in handler.call_args_list)
    assert guarded.messages is request.messages
    assert guarded.model.metadata == request.model.metadata
    assert request.model.callbacks is None
    assert delays == [2, 4, 8, 10, 10, 10, 10, 10, 10, 10]


@pytest.mark.parametrize("error", [
    ModelTimeoutError("timed out"),
    APITimeoutError(request=httpx.Request("POST", "https://model.test")),
    httpx.ReadTimeout("timed out"),
])
def test_only_three_timeout_retries(monkeypatch, error):
    sleep = Mock()
    monkeypatch.setattr("vertical_harness.middleware.model_retry.time.sleep", sleep)
    handler = Mock(side_effect=error)
    with pytest.raises(type(error)):
        OpenCodeModelRetryMiddleware().wrap_model_call(model_request(), handler)
    assert handler.call_count == 4
    assert sleep.call_count == 3


@pytest.mark.parametrize("error", [
    ValueError("unexpected"),
    ModelInvalidRequestError("invalid"),
    status_error(400), status_error(401), status_error(403), status_error(402),
    status_error(429, code="insufficient_quota"),
    status_error(429, message="quota exceeded"),
    status_error(503, headers={"x-should-retry": "false"}),
    GraphInterrupt(),
])
def test_deterministic_errors_and_graph_interrupts_propagate(error):
    handler = Mock(side_effect=error)
    with pytest.raises(type(error)) as raised:
        OpenCodeModelRetryMiddleware().wrap_model_call(model_request(), handler)
    assert raised.value is error
    handler.assert_called_once()


@pytest.mark.parametrize("error", [
    status_error(408), status_error(409), status_error(429), status_error(500),
    status_error(400, headers={"x-should-retry": "true"}),
    ModelAPIError("provider failure"), httpx.ConnectError("connection failed"),
])
def test_expected_transient_errors_recover(monkeypatch, error):
    monkeypatch.setattr("vertical_harness.middleware.model_retry.time.sleep", Mock())
    response = object()
    handler = Mock(side_effect=[error, response])
    assert OpenCodeModelRetryMiddleware().wrap_model_call(model_request(), handler) is response
    assert handler.call_count == 2


@pytest.mark.parametrize("headers,expected", [
    ({"retry-after": "20"}, 20),
    ({"retry-after-ms": "2500"}, 2.5),
    ({"retry-after": "999999"}, 900),
    ({"retry-after": "-3"}, 2),
    ({"retry-after": "invalid"}, 2),
    ({"retry-after": "nan"}, 2),
    ({"retry-after-ms": "invalid", "retry-after": "20"}, 20),
    ({"retry-after-ms": "nan", "retry-after": "20"}, 20),
    ({"retry-after": "Wed, 21 Oct 2026 00:00:20 GMT"}, 20),
])
def test_retry_after_and_cap(monkeypatch, headers, expected):
    monkeypatch.setattr("vertical_harness.middleware.model_retry.random.uniform", lambda *_: 1)
    monkeypatch.setattr("vertical_harness.middleware.model_retry.time.time", lambda: 1792540800)
    assert OpenCodeModelRetryMiddleware._delay(status_error(429, headers=headers), 0) == expected


def test_async_cancellation_propagates_without_retry():
    handler = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(OpenCodeModelRetryMiddleware().awrap_model_call(model_request(), handler))
    handler.assert_awaited_once()


def test_retry_wait_is_cancellable(monkeypatch):
    handler = AsyncMock(side_effect=status_error(503))
    sleep = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr("vertical_harness.middleware.model_retry.asyncio.sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(OpenCodeModelRetryMiddleware().awrap_model_call(model_request(), handler))
    handler.assert_awaited_once()


def test_timeout_budget_is_local_to_model_call(monkeypatch):
    monkeypatch.setattr("vertical_harness.middleware.model_retry.time.sleep", Mock())
    middleware = OpenCodeModelRetryMiddleware()
    for _ in range(2):
        handler = Mock(side_effect=[ModelTimeoutError("timeout"), "response"])
        assert middleware.wrap_model_call(model_request(), handler) == "response"


@pytest.mark.parametrize("execution", ["sync", "async"])
def test_delivered_output_prevents_replay(execution):
    handler = Mock(side_effect=emit_then_fail) if execution == "sync" else AsyncMock(side_effect=emit_then_fail)
    middleware = OpenCodeModelRetryMiddleware()
    with pytest.raises(APIStatusError):
        if execution == "sync":
            middleware.wrap_model_call(model_request(), handler)
        else:
            asyncio.run(middleware.awrap_model_call(model_request(), handler))
    handler.assert_called_once()


class OfflineSSEStream(httpx.SyncByteStream, httpx.AsyncByteStream):
    def __init__(self, *, broken, content):
        self.broken = broken
        self.content = content

    def __iter__(self):
        yield self.event({"role": "assistant", "content": ""})
        if self.content:
            yield self.event({"content": self.content})
        if self.broken:
            raise httpx.ReadError("stream disconnected")
        yield self.event({}, finish="stop")
        yield b"data: [DONE]\n\n"

    async def __aiter__(self):
        for data in self:
            yield data

    @staticmethod
    def event(delta, finish=None):
        return ("data: " + json.dumps({
            "id": "offline", "object": "chat.completion.chunk", "created": 1, "model": "offline",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }) + "\n\n").encode()


class OfflineSSEProvider:
    def __init__(self, content, *, failures=1):
        self.requests = 0
        self.content = content
        self.failures = failures
        self.bodies = []

    def __call__(self, request):
        self.requests += 1
        self.bodies.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=OfflineSSEStream(
            broken=self.requests <= self.failures,
            content=self.content if self.requests <= self.failures else "recovered",
        ))


class TokenRecorder(BaseCallbackHandler):
    run_inline = True

    def __init__(self):
        self.tokens = []

    def on_llm_new_token(self, token, **kwargs):
        self.tokens.append(token)


async def consume_offline_stream(provider, chunks, callbacks):
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        model = ChatOpenAI(
            model="offline", api_key="offline", base_url="https://offline.test/v1",
            http_async_client=client, max_retries=0, callbacks=callbacks, metadata={"preserved": True},
        )
        agent = create_agent(model, middleware=[OpenCodeModelRetryMiddleware()], checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "stream-recovery"}}
        async for chunk, _ in agent.astream({"messages": [("user", "hello")]}, config, stream_mode="messages"):
            chunks.append(chunk.content)
        assert model.callbacks == callbacks
        assert model.metadata["preserved"] is True
        return (await agent.aget_state(config)).values["messages"]


def test_sdk_stream_continues_from_checkpointed_partial_content(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("vertical_harness.middleware.model_retry.asyncio.sleep", sleep)
    provider = OfflineSSEProvider("partial")
    chunks = []
    recorder = TokenRecorder()
    messages = asyncio.run(consume_offline_stream(provider, chunks, [recorder]))
    assert provider.requests == 2
    assert chunks.count("partial") == 1
    assert recorder.tokens.count("partial") == 1
    assert chunks.count("recovered") == 1
    assert [message.content for message in messages] == [
        "hello", "partial", OpenCodeModelRetryMiddleware._continuation, "recovered",
    ]
    assert messages[1].additional_kwargs["opencode_stream_retry"] == {"attempt": 1, "timeouts": 0}
    assert messages[2].additional_kwargs["opencode_synthetic"] == "interrupted-stream"
    sent = provider.bodies[1]["messages"]
    assert sent[-2] == {"role": "assistant", "content": "partial"}
    assert sent[-1]["content"] == OpenCodeModelRetryMiddleware._continuation
    sleep.assert_awaited_once()


def test_sync_sdk_stream_continues_without_replaying_partial_content(monkeypatch):
    sleep = Mock()
    monkeypatch.setattr("vertical_harness.middleware.model_retry.time.sleep", sleep)
    provider = OfflineSSEProvider("partial")
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        model = ChatOpenAI(
            model="offline", api_key="offline", base_url="https://offline.test/v1",
            http_client=client, max_retries=0,
        )
        agent = create_agent(model, middleware=[OpenCodeModelRetryMiddleware()], checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "sync-stream-recovery"}}
        chunks = [chunk.content for chunk, _ in agent.stream(
            {"messages": [("user", "hello")]}, config, stream_mode="messages",
        )]
        messages = agent.get_state(config).values["messages"]
    assert provider.requests == 2
    assert chunks.count("partial") == 1 and chunks.count("recovered") == 1
    assert [message.content for message in messages] == [
        "hello", "partial", OpenCodeModelRetryMiddleware._continuation, "recovered",
    ]
    assert provider.bodies[1]["messages"][-2] == {"role": "assistant", "content": "partial"}
    sleep.assert_called_once()


def test_stream_continuations_share_retry_budget(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("vertical_harness.middleware.model_retry.asyncio.sleep", sleep)
    monkeypatch.setattr("vertical_harness.middleware.model_retry.random.uniform", lambda *_: 1)
    provider = OfflineSSEProvider("partial", failures=11)
    with pytest.raises(ModelConnectionError):
        asyncio.run(consume_offline_stream(provider, [], []))
    assert provider.requests == 11
    assert [call.args[0] for call in sleep.await_args_list] == [2, 4, 8, 10, 10, 10, 10, 10, 10, 10]


def test_new_user_input_resets_stream_retry_budget():
    partial = AIMessage(content="partial", additional_kwargs={
        "opencode_stream_retry": {"attempt": 10, "timeouts": 3},
    })
    continuation = HumanMessage(content="continue", additional_kwargs={"opencode_synthetic": "interrupted-stream"})
    request = model_request().override(messages=[partial, continuation])
    assert OpenCodeModelRetryMiddleware._retry_state(request) == (10, 3)
    assert OpenCodeModelRetryMiddleware._retry_state(
        request.override(messages=[partial, continuation, HumanMessage(content="new task")])
    ) == (0, 0)


def test_incomplete_streamed_tool_calls_are_not_executed_or_replayed():
    with ModelOutputGuard() as guard:
        guard.on_llm_new_token("text", chunk=ChatGenerationChunk(message=AIMessageChunk(
            content="text", tool_call_chunks=[{"name": "write_file", "args": '{"path":', "id": "call-1", "index": 0}],
        )))
        assert guard.delivered
        assert OpenCodeModelRetryMiddleware._partial(guard, httpx.ReadError("broken"), 1, 0) is None


def test_sdk_stream_retries_failure_before_content(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("vertical_harness.middleware.model_retry.asyncio.sleep", sleep)
    provider = OfflineSSEProvider("")
    chunks = []
    recorder = TokenRecorder()
    asyncio.run(consume_offline_stream(provider, chunks, [recorder]))
    assert provider.requests == 2
    assert "recovered" in chunks
    assert recorder.tokens.count("recovered") == 1
    sleep.assert_awaited_once()


@pytest.mark.parametrize("managed", [False, True])
def test_guard_preserves_existing_callbacks_and_metadata(managed):
    recorder = TokenRecorder()
    callbacks = CallbackManager([recorder], tags=["original"], metadata={"trace": 1}) if managed else [recorder]
    request = model_request()
    request.model.callbacks = callbacks
    original_metadata = dict(request.model.metadata)
    baseline = CallbackManager.configure(local_callbacks=callbacks)
    with ModelOutputGuard() as guard:
        configured = CallbackManager.configure(local_callbacks=callbacks)
        assert recorder in configured.handlers
        assert guard in configured.handlers
    assert guard not in CallbackManager.configure().handlers
    assert request.model.callbacks is callbacks
    assert request.model.metadata == original_metadata
    if managed:
        assert configured.tags == baseline.tags
        assert configured.metadata == baseline.metadata
        assert callbacks.tags == ["original"]
        assert callbacks.metadata == {"trace": 1}
        assert callbacks.handlers == [recorder]
    else:
        assert callbacks == [recorder]


def test_stateful_model_keeps_advancing_between_graph_turns():
    model = FakeListChatModel(responses=["first", "second", "third"])
    agent = create_agent(model, middleware=[OpenCodeModelRetryMiddleware()])
    first = agent.invoke({"messages": [("user", "first question")]})
    second = agent.invoke({"messages": [("user", "second question")]})
    assert first["messages"][-1].content == "first"
    assert second["messages"][-1].content == "second"
    assert model.i == 2
    assert model.callbacks is None


class ConcurrentModelCalls:
    def __init__(self):
        self.delivered = asyncio.Event()
        self.finished = asyncio.Event()
        self.calls = {"output": 0, "quiet": 0}

    async def __call__(self, request):
        identity = request.model_settings["identity"]
        self.calls[identity] += 1
        if identity == "output":
            manager = CallbackManager.configure()
            manager.on_llm_start({"name": "test"}, ["input"])[0].on_llm_new_token("partial")
            self.delivered.set()
            await self.finished.wait()
            raise status_error(503)
        await self.delivered.wait()
        if self.calls[identity] == 1:
            raise status_error(503)
        self.finished.set()
        return "recovered"


async def run_concurrent_calls():
    handler = ConcurrentModelCalls()
    output = model_request()
    output.model_settings["identity"] = "output"
    quiet = ModelRequest(model=output.model, messages=[], model_settings={"identity": "quiet"})
    assert output.model is quiet.model
    middleware = OpenCodeModelRetryMiddleware()
    results = await asyncio.gather(
        middleware.awrap_model_call(output, handler),
        middleware.awrap_model_call(quiet, handler),
        return_exceptions=True,
    )
    assert isinstance(results[0], APIStatusError)
    assert results[1] == "recovered"
    assert handler.calls == {"output": 1, "quiet": 2}
    assert not any(isinstance(item, ModelOutputGuard) for item in CallbackManager.configure().handlers)


def test_concurrent_calls_have_separate_delivery_state(monkeypatch):
    monkeypatch.setattr("vertical_harness.middleware.model_retry.asyncio.sleep", AsyncMock())
    asyncio.run(run_concurrent_calls())


@pytest.mark.parametrize("error", [None, ValueError("failed"), asyncio.CancelledError()])
def test_guard_context_is_reset_after_success_failure_or_cancellation(error):
    expected = pytest.raises(type(error)) if error is not None else nullcontext()
    with expected, ModelOutputGuard() as guard:
        assert guard in CallbackManager.configure().handlers
        if error is not None:
            raise error
    assert guard not in CallbackManager.configure().handlers
