from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from vertical_harness import HarnessSettings, build_agent
from vertical_harness.engine import compose


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_partial_read_and_exact_edit_preserve_unrelated_content(tmp_path, monkeypatch):
    original = "\n".join(f"value_{i} = {i}" for i in range(300)) + "\n"
    script = tmp_path / "analysis.py"
    script.write_text(original, encoding="utf-8")
    model = ToolModel(responses=[
        AIMessage(content="", tool_calls=[{
            "id": "read", "name": "read_file",
            "args": {"file_path": "/analysis.py", "offset": 149, "limit": 3},
        }]),
        AIMessage(content="", tool_calls=[{
            "id": "edit", "name": "edit_file",
            "args": {"file_path": "/analysis.py", "old_string": "value_150 = 150",
                     "new_string": "value_150 = 42"},
        }]),
        AIMessage(content="Done"),
    ])
    monkeypatch.setattr(compose, "init_configured_model", lambda _settings: model)
    settings = HarnessSettings(_env_file=None, model="test:edit", workspace_root=tmp_path,
                               store_backend="memory", enable_memory=False)
    agent = build_agent(settings, plugins=[], mcp_tools=[], checkpointer=False)
    result = agent.invoke({"messages": [HumanMessage(content="Update one value")]})
    messages = {m.tool_call_id: m for m in result["messages"] if isinstance(m, ToolMessage)}

    assert messages["edit"].status == "success"
    assert "value_149 = 149" in messages["read"].content
    assert "value_151 = 151" in messages["read"].content
    assert "value_148 = 148" not in messages["read"].content
    assert "value_152 = 152" not in messages["read"].content
    assert script.read_text(encoding="utf-8") == original.replace("value_150 = 150", "value_150 = 42")


def test_search_is_bounded_and_ambiguous_or_private_edits_do_not_write(tmp_path, monkeypatch):
    original = "\n".join(f"match {i}" for i in range(150)) + "\n"
    (tmp_path / "many.txt").write_text(original, encoding="utf-8")
    (tmp_path / ".env").write_text("PRIVATE_SENTINEL", encoding="utf-8")
    model = ToolModel(responses=[
        AIMessage(content="", tool_calls=[{
            "id": "search", "name": "grep",
            "args": {"pattern": "match", "path": "/", "output_mode": "content"},
        }]),
        AIMessage(content="", tool_calls=[{
            "id": "ambiguous", "name": "edit_file",
            "args": {"file_path": "/many.txt", "old_string": "match", "new_string": "changed"},
        }]),
        AIMessage(content="", tool_calls=[{
            "id": "private", "name": "edit_file",
            "args": {"file_path": "/.env", "old_string": "PRIVATE_SENTINEL", "new_string": "changed"},
        }]),
        AIMessage(content="Done"),
    ])
    monkeypatch.setattr(compose, "init_configured_model", lambda _settings: model)
    settings = HarnessSettings(_env_file=None, model="test:search", workspace_root=tmp_path,
                               store_backend="memory", enable_memory=False)
    result = build_agent(settings, plugins=[], mcp_tools=[], checkpointer=False).invoke({
        "messages": [HumanMessage(content="Find and edit text")],
    })
    messages = {m.tool_call_id: m for m in result["messages"] if isinstance(m, ToolMessage)}

    assert "match 99" in messages["search"].content
    assert "match 100" not in messages["search"].content
    assert "incomplete" in messages["search"].content.lower()
    assert "PRIVATE_SENTINEL" not in messages["search"].content
    assert messages["ambiguous"].status == "error"
    assert messages["private"].status == "error"
    assert (tmp_path / "many.txt").read_text(encoding="utf-8") == original
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "PRIVATE_SENTINEL"
