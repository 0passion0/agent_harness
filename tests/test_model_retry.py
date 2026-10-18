"""Exercise configured model retries through real SDKs and in-memory HTTP transport."""

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import openai
import openai._base_client as sdk
import pytest

from vertical_harness import HarnessSettings
from vertical_harness.engine import routing


@pytest.mark.parametrize("mode", ["invoke", "ainvoke", "stream", "astream"])
@pytest.mark.parametrize("failure", ["connection", "500", "timeout", "401"])
def test_model_retry_limit_backoff_and_permanent_errors(monkeypatch, mode, failure):
    requests = []
    delays = []

    def handle(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("network timeout", request=request)
        if failure == "401" or len(requests) <= 5:
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

    monkeypatch.setattr(sdk, "time", SimpleNamespace(sleep=delays.append, time=time.time))
    monkeypatch.setattr(sdk, "anyio", SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(sdk, "random", lambda: 0)
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
                model = routing.init_configured_model(
                    HarnessSettings(
                        _env_file=None,
                        model="deepseek:deepseek-chat",
                        api_key="test",
                        base_url="https://model.test",
                    )
                )
                if mode == "invoke":
                    return model.invoke("hello").content
                if mode == "ainvoke":
                    return (await model.ainvoke("hello")).content
                if mode == "stream":
                    return "".join(chunk.content for chunk in model.stream("hello"))
                return "".join([chunk.content async for chunk in model.astream("hello")])

    error = {"timeout": openai.APITimeoutError, "401": openai.AuthenticationError}.get(failure)
    if error:
        with pytest.raises(error):
            asyncio.run(run())
    else:
        assert asyncio.run(run()) == "recovered"
    assert len(requests) == (1 if failure == "401" else 6)
    assert delays == ([] if failure == "401" else [0.5, 1, 2, 4, 8])
