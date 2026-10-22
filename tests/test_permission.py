"""Sync and async tool entry points enforce the same read-only boundary."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain.agents.middleware import ModelRequest

from vertical_harness.middleware.permission import DesktopPermissionMiddleware


@pytest.mark.parametrize("use_async", [False, True])
@pytest.mark.parametrize(
    "mode,tool,allowed",
    [
        ("read_only", "read_file", True),
        ("read_only", "write_todos", True),
        ("read_only", "shell", False),
        ("read_only", "task", False),
        ("read_only", "unknown_tool", False),
        ("ask", "shell", True),
        ("full", "shell", True),
    ],
)
def test_permission_checks_precede_handler(mode, tool, allowed, use_async):
    middleware = DesktopPermissionMiddleware(mode)
    request = SimpleNamespace(tool=SimpleNamespace(name=tool))
    handler = AsyncMock(return_value="done") if use_async else Mock(return_value="done")
    if allowed:
        result = (
            asyncio.run(middleware.awrap_tool_call(request, handler))
            if use_async
            else middleware.wrap_tool_call(request, handler)
        )
        assert result == "done"
        handler.assert_called_once_with(request)
        if use_async:
            handler.assert_awaited_once_with(request)
    else:
        with pytest.raises(PermissionError, match=f"Tool {tool} is disabled in read_only mode"):
            if use_async:
                asyncio.run(middleware.awrap_tool_call(request, handler))
            else:
                middleware.wrap_tool_call(request, handler)
        handler.assert_not_called()


@pytest.mark.parametrize("use_async", [False, True])
@pytest.mark.parametrize("mode", ["read_only", "ask", "full"])
def test_tool_filter_handles_dict_schemas_without_mutating_request(mode, use_async):
    tools = [
        {"name": "read_file", "parameters": {"type": "object"}},
        {"type": "function", "function": {"name": "grep", "parameters": {"type": "object"}}},
        {"type": "function", "function": {"name": "shell", "parameters": {"type": "object"}}},
        {"type": "unknown_provider_tool"},
    ]
    request = ModelRequest(model=Mock(), messages=[], tools=tools)
    middleware = DesktopPermissionMiddleware(mode)
    handler = AsyncMock(return_value="done") if use_async else Mock(return_value="done")
    result = (
        asyncio.run(middleware.awrap_model_call(request, handler))
        if use_async else middleware.wrap_model_call(request, handler)
    )
    assert result == "done"
    prepared = handler.call_args.args[0]
    if mode == "read_only":
        assert prepared.tools == tools[:2]
        assert all(left is right for left, right in zip(prepared.tools, tools, strict=False))
    else:
        assert prepared is request
    assert request.tools is tools and len(tools) == 4
