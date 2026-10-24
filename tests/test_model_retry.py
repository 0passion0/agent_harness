"""Exercise configured model retries through real SDKs and in-memory HTTP transport."""

import asyncio
import json

import httpx
import openai
import pytest
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from vertical_harness.core import routing
from vertical_harness.middleware.model_retry import ModelRetryMiddleware
from vertical_harness.models import HarnessSettings


@pytest.mark.parametrize("mode", ["invoke", "ainvoke", "stream", "astream"])
@pytest.mark.parametrize("failure", ["connection", "500", "timeout", "401"])
def test_model_retry_limit_backoff_and_permanent_errors(monkeypatch, mode, failure):
    requests = []
    delays = []

    def handle(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("network timeout", request=request)
        if failure == "401" or len(requests) <= 10:
            if failure == "connection":
                raise httpx.ConnectError("network unavailable", request=request)
            return httpx.Response(
                int(failure), json={"error": {"message": "request failed", "type": "api_error"}}
            )
        if json.loads(request.content).get("stream"):
            chunk = {
                "id": "reply",
                "model": "deepseek-chat",
                "choices": [{"index": 0, "delta": {"content": "recovered"}}],
            }
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            json={
                "id": "reply",
                "object": "chat.completion",
                "model": "deepseek-chat",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "recovered"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("vertical_harness.middleware.model_retry.time.sleep", delays.append)
    monkeypatch.setattr("vertical_harness.middleware.model_retry.asyncio.sleep", sleep)
    monkeypatch.setattr("vertical_harness.middleware.model_retry.random.uniform", lambda *_: 1)
    original_init = routing.init_chat_model

    async def run():
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as async_client:
                monkeypatch.setattr(
                    routing,
                    "init_chat_model",
                    lambda name, **kwargs: original_init(
                        name, http_client=client, http_async_client=async_client, **kwargs
                    ),
                )
                model = routing.ModelRouting.init_configured_model(
                    HarnessSettings(
                        _env_file=None,
                        model="deepseek:deepseek-chat",
                        api_key="test",
                        base_url="https://model.test",
                    )
                )
                assert model.max_retries == 0
                agent = create_agent(model=model, middleware=[ModelRetryMiddleware()])
                request = {"messages": [HumanMessage(content="hello")]}
                if mode == "invoke":
                    return agent.invoke(request)["messages"][-1].content
                if mode == "ainvoke":
                    return (await agent.ainvoke(request))["messages"][-1].content
                if mode == "stream":
                    values = list(agent.stream(request, stream_mode="values"))
                else:
                    values = [value async for value in agent.astream(request, stream_mode="values")]
                return values[-1]["messages"][-1].content

    error = {"timeout": openai.APITimeoutError, "401": openai.AuthenticationError}.get(failure)
    if error:
        with pytest.raises(error):
            asyncio.run(run())
    else:
        assert asyncio.run(run()) == "recovered"
    count = {"401": 1, "timeout": 4}.get(failure, 11)
    assert len(requests) == count
    assert delays == [2, 4, 8, *([10] * 7)][:count - 1]
