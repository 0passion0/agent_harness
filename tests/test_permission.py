"""Sync and async tool entry points enforce the same read-only boundary."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

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
