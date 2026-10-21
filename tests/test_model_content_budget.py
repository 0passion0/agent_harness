from deepagents.middleware.unsupported_content import UnsupportedContentMiddleware
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_deepseek import ChatDeepSeek

from vertical_harness.config.settings import HarnessSettings
from vertical_harness.core import routing


def test_deepseek_tool_images_are_filtered_before_payload_serialization(monkeypatch):
    model = ChatDeepSeek(model="deepseek-flash", api_key="offline-test-key")
    original_profile = dict(model.profile)
    monkeypatch.setattr(routing, "init_chat_model", lambda *_args, **_kwargs: model)
    configured = routing.ModelRouting.init_configured_model(
        HarnessSettings(_env_file=None, model="deepseek:deepseek-flash")
    )
    assert configured.profile["image_inputs"] == original_profile["image_inputs"]
    assert configured.profile["max_input_tokens"] == original_profile["max_input_tokens"]
    image = {"type": "image", "base64": "BINARY_SENTINEL" * 30000, "mime_type": "image/png"}
    tool_message = ToolMessage(
        content=[image], tool_call_id="chart", additional_kwargs={"read_file_path": "/chart.png"}
    )
    human_image = HumanMessage(
        content=[{"type": "image", "base64": "USER_IMAGE", "mime_type": "image/png"}]
    )
    request = ModelRequest(model=configured, messages=[human_image, tool_message], tools=[])
    seen = []
    UnsupportedContentMiddleware().wrap_model_call(
        request, lambda filtered: seen.extend(filtered.messages)
    )
    payload = model._get_request_payload(seen)
    tool_content = payload["messages"][-1]["content"]
    assert len(tool_content) < 1000
    assert "BINARY_SENTINEL" not in tool_content
    assert "/chart.png" in tool_content
    assert seen[0] is human_image
    assert tool_message.content[0]["base64"] == image["base64"]


def test_agent_bounds_file_reads_and_preserves_private_path_denial(tmp_path, monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from vertical_harness.core import compose

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    original = "\n".join(f"row-{i:03d}: " + "x" * 1000 for i in range(100))
    (tmp_path / "large.txt").write_text(original, encoding="utf-8")
    (tmp_path / "private.sqlite").write_text("PRIVATE_SENTINEL", encoding="utf-8")
    model = Model(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "large",
                        "name": "read_file",
                        "args": {"file_path": "/large.txt", "limit": 100},
                    },
                    {
                        "id": "private",
                        "name": "read_file",
                        "args": {"file_path": "/private.sqlite"},
                    },
                ],
            ),
            AIMessage(content="Done"),
        ]
    )
    monkeypatch.setattr(compose.ModelRouting, "init_configured_model", lambda _settings: model)
    settings = HarnessSettings(
        _env_file=None,
        model="test:bounded",
        workspace_root=tmp_path,
        store_backend="memory",
        store_db_path=tmp_path / "private.sqlite",
        enable_memory=False,
        enable_user_preferences=False,
    )
    agent = compose.AgentComposer.build_agent(settings)
    result = agent.invoke(
        {"messages": [HumanMessage(content="Read files")]},
        {"configurable": {"thread_id": "bounded"}},
    )
    tools = {
        message.tool_call_id: message.content
        for message in result["messages"]
        if isinstance(message, ToolMessage)
    }
    assert len(tools["large"]) < 12500
    assert "row-000:" in tools["large"]
    assert "row-099:" not in tools["large"]
    assert "next offset 11" in tools["large"]
    assert "PRIVATE_SENTINEL" not in tools["private"]
    assert "denied" in tools["private"].lower()
    assert (tmp_path / "large.txt").read_text(encoding="utf-8") == original
