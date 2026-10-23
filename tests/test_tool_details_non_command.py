"""测试非 Command 与复杂对象作为工具返回值时的 ToolDetails 及 AgentStreaming 事件处理。"""

import asyncio
from uuid import uuid4

from vertical_harness.langchain.streaming import AgentStreaming
from vertical_harness.langchain.tool_details import ToolDetails


class FakeComplexResponse:
    """模拟三方工具（如 Exa SearchResponse）返回的无 content 属性的复合对象。"""

    def __init__(self, results: list[str]) -> None:
        self.results = results

    def __str__(self) -> str:
        return f"FakeComplexResponse(results={self.results})"


def test_tool_details_handles_strings_and_complex_objects() -> None:
    """验证 ToolDetails 对普通字符串、字典、复杂对象的记录与耗时计算不会崩溃。"""
    async def scenario() -> None:
        # 第一步：初始化 ToolDetails 实例
        details = ToolDetails(475_000)

        # 第二步：测试字符串类型工具输出（如 WebSearch 或 ask_user）
        str_run_id = uuid4()
        await details.on_tool_start({"name": "web_search"}, "query", run_id=str_run_id, inputs={"query": "test"})
        await details.on_tool_end("plain string result", run_id=str_run_id)
        assert details.records[str(str_run_id)]["output"] == "plain string result"
        assert details.records[str(str_run_id)]["elapsed"] >= 0

        # 第三步：测试第三方复杂对象输出（如 Exa SearchResponse）
        obj_run_id = uuid4()
        complex_obj = FakeComplexResponse(["title1", "title2"])
        await details.on_tool_start({"name": "exa_search"}, "query", run_id=obj_run_id, inputs={"query": "test"})
        await details.on_tool_end(complex_obj, run_id=obj_run_id)
        assert details.records[str(obj_run_id)]["output"] == str(complex_obj)
        assert details.records[str(obj_run_id)]["elapsed"] >= 0

    asyncio.run(scenario())


def test_agent_streaming_project_handles_non_command_tool_end() -> None:
    """验证 AgentStreaming._project 对非 ToolMessage 工具输出正常投影，不抛 AttributeError。"""
    # 第一步：模拟复杂对象的 on_tool_end 事件
    raw_event = {
        "event": "on_tool_end",
        "run_id": "tool-run-id",
        "data": {"output": FakeComplexResponse(["doc1"])},
        "metadata": {},
        "name": "exa_search_results_json",
    }
    projected = AgentStreaming._project(raw_event, "root-id", ())
    assert projected is not None
    event_type, _data = projected
    assert event_type == "tool.completed"

    # 第二步：模拟字符串工具输出的 on_tool_end 事件
    raw_str_event = {
        "event": "on_tool_end",
        "run_id": "tool-run-id-2",
        "data": {"output": "some result text"},
        "metadata": {},
        "name": "web_search",
    }
    projected_str = AgentStreaming._project(raw_str_event, "root-id", ())
    assert projected_str is not None
    assert projected_str[0] == "tool.completed"
