import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from vertical_harness import desktop as bridge


def test_streamed_tool_details_reach_the_original_cards(monkeypatch, tmp_path):
    @tool
    def write_note(content: str) -> str:
        """Write a note in the test workspace."""
        (tmp_path / "note.txt").write_text(content, encoding="utf-8")
        return "saved"

    def respond(state):
        if isinstance(state["messages"][-1], ToolMessage):
            return {"messages": [AIMessage(content="done")]}
        return {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "note-call",
                            "name": "write_note",
                            "args": {"content": "中文内容"},
                        }
                    ],
                )
            ]
        }

    graph = StateGraph(MessagesState)
    graph.add_node("model", respond)
    graph.add_node("tools", ToolNode([write_note]))
    graph.add_edge(START, "model")
    graph.add_conditional_edges(
        "model", lambda state: "tools" if state["messages"][-1].tool_calls else END
    )
    graph.add_edge("tools", "model")
    app = graph.compile().with_config({"recursion_limit": 9999})

    @asynccontextmanager
    async def open_agent(_state):
        yield app

    frames = []

    async def capture(_state, method, params):
        frames.append(params)

    async def scenario():
        monkeypatch.setenv("VA_MODEL", "test:fake")
        await bridge.loop.dispatch(
            "configure",
            {
                "tenant_id": "tool-test",
                "user_id": "7",
                "workspace_root": str(tmp_path),
                "runtime_data_dir": str(tmp_path / "runtime"),
                "options": {
                    "model_code": "",
                    "runtimes": {"python": "", "node": ""},
                    "permission_mode": "ask",
                    "max_context_tokens": 128000,
                    "agent_content": "",
                    "skills": [],
                    "mcp_servers": {},
                    "enabled_plugins": [],
                },
            },
        )
        monkeypatch.setattr(bridge, "open_configured_agent", open_agent)
        monkeypatch.setattr(bridge, "notify", capture)
        thread_id, run_id = str(uuid4()), str(uuid4())
        await bridge.loop._run(thread_id, run_id, {"messages": [HumanMessage(content="write")]})

    asyncio.run(scenario())
    start = next(frame for frame in frames if frame["type"] == "tool.started")
    end = next(frame for frame in frames if frame["type"] == "tool.completed")
    assert start["operation_id"] == end["operation_id"]
    assert start["data"]["arguments"] == {"content": "中文内容"}
    assert end["data"]["arguments"] == start["data"]["arguments"]
    assert end["data"]["output"] == "saved"
    assert end["data"]["elapsed"] >= 0
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "中文内容"
