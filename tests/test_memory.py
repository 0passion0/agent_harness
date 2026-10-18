"""使用真实 LangMem 提取器和 SQLite 验证记忆，不调用外部模型。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.store.memory import InMemoryStore
from pydantic import Field, ValidationError

from vertical_harness import HarnessSettings, RunContext, aopen_agent, build_agent, cli, open_store
from vertical_harness.engine import compose, memory


class RecordingModel(FakeMessagesListChatModel):
    seen: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        self.seen.append(messages)
        return super()._generate(messages, *args, **kwargs)


def settings(tmp_path, **kwargs):
    return HarnessSettings(
        _env_file=None,
        model="test:model",
        workspace_root=tmp_path,
        enabled_plugins=[],
        enable_planning=False,
        **kwargs,
    )


def runtime(user="user", tenant="tenant"):
    return SimpleNamespace(context=RunContext(tenant_id=tenant, user_id=user))


@pytest.mark.parametrize("async_mode", [False, True])
def test_large_memory_disclosure_is_lossless_and_scoped(tmp_path, monkeypatch, async_mode):
    from langchain.agents.middleware import ModelRequest
    from langchain_core.messages import ToolMessage

    config = settings(tmp_path, store_backend="memory", enable_user_preferences=False)
    store = InMemoryStore()
    facts = [f"fact {index}: " + "证据不能丢失" * 75 for index in range(20)]
    ns = memory._namespace(runtime())
    store.put((*ns, "knowledge"), "profile", {"facts": facts})
    model = RecordingModel(responses=[
        AIMessage(content="", tool_calls=[{"name": "recall_user_memory", "args": {}, "id": "recall"}]),
        AIMessage(content="done"),
    ])
    monkeypatch.setattr(compose, "init_configured_model", lambda *_: model)
    agent = build_agent(config, store=store)
    kwargs = {"config": {"configurable": {"thread_id": "memory-index"}}, "context": runtime().context}
    result = asyncio.run(agent.ainvoke(state("use project facts"), **kwargs)) if async_mode else agent.invoke(state("use project facts"), **kwargs)
    assert len(model.seen[0][0].text) < len(memory._memory_json({"facts": facts})) // 2
    assert "recall_user_memory" in model.seen[0][0].text
    output = next(message for message in result["messages"] if isinstance(message, ToolMessage))
    assert all(fact in output.text for fact in facts)
    # The complete profile is already in model history; do not repeat its index in the system prompt.
    assert "fact_previews" not in model.seen[1][0].text

    middleware = memory.UserMemoryMiddleware(config, store)
    request = ModelRequest(model=model, messages=[], tools=middleware.tools, runtime=runtime("other"))
    other = middleware._recall(request)
    assert not other.tools and other.system_message is None
    assert middleware._read_knowledge(runtime("other")).endswith('{"facts":[]}')
    with pytest.raises(ValueError, match="trusted"):
        middleware._read_knowledge(SimpleNamespace(context=None))
    cleared = middleware._recall(request.override(messages=[output.model_copy(update={"content": "[cleared]"})], runtime=runtime()))
    assert "fact_previews" in cleared.system_message.text
    # Once a profile changes, an old read must not suppress fresh recall.
    store.put((*ns, "knowledge"), "profile", {"facts": [*facts, "new fact"]})
    fresh = middleware._recall(request.override(messages=[output], runtime=runtime()))
    assert "new fact" in fresh.system_message.text
    assert fresh.tools


def test_small_memory_and_inline_opt_out_keep_complete_facts(tmp_path):
    from langchain.agents.middleware import ModelRequest

    store = InMemoryStore()
    profile = {"facts": ["An exact fact with a decisive detail."]}
    store.put((*memory._namespace(runtime()), "knowledge"), "profile", profile)
    middleware = memory.UserMemoryMiddleware(settings(tmp_path, memory_inline_max_chars=0), store)
    request = ModelRequest(model=RecordingModel(responses=[AIMessage(content="ok")]), messages=[], tools=middleware.tools, runtime=runtime())
    recalled = middleware._recall(request)
    assert profile["facts"][0] in recalled.system_message.text
    assert not recalled.tools


def state(text="请始终使用中文", id="message-1"):
    return {"messages": [HumanMessage(content=text, id=id)]}


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")


@pytest.mark.parametrize("async_mode", [False, True])
def test_real_langmem_preferences_persist_recall_and_update(tmp_path, monkeypatch, async_mode):
    config = settings(tmp_path, enable_user_preferences=True)
    main_model = RecordingModel(responses=[AIMessage(content="ok")])
    preference_model = RecordingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "PatchDoc",
                        "id": "patch-1",
                        "args": {
                            "json_doc_id": "profile",
                            "planned_edits": "Set explicit language",
                            "patches": [
                                {"op": "replace", "path": "/preferences", "value": [value]}
                            ],
                        },
                    }
                ],
            )
            for value in ["始终使用中文", "使用英文"]
        ]
    )
    monkeypatch.setattr(compose, "init_configured_model", lambda *_: main_model)
    monkeypatch.setattr(memory, "init_configured_model", lambda *_: preference_model)

    def invoke(agent, thread, text, user="user"):
        return agent.invoke(
            state(text, id=thread),
            config={"configurable": {"thread_id": thread}},
            context=runtime(user).context,
        )

    async def ainvoke(thread, text, user="user"):
        # 每次重开宿主和数据库，验证不是进程内缓存。
        async with aopen_agent(config) as agent:
            return await agent.ainvoke(
                state(text, id=thread),
                config={"configurable": {"thread_id": thread}},
                context=runtime(user).context,
            )

    def run(thread, text, user="user"):
        if async_mode:
            return asyncio.run(ainvoke(thread, text, user))
        with open_store(config) as store:
            return invoke(build_agent(config, store=store), thread, text, user)

    run("first", "请始终使用中文")
    assert "始终使用中文" in main_model.seen[-1][0].text
    run("second", "现在改用英文")
    assert "使用英文" in main_model.seen[-1][0].text
    # 无用户输入的召回不会再次提取；其他用户不能读取此偏好。
    with open_store(config) as store:
        middleware = memory.UserMemoryMiddleware(config, store)
        request = SimpleNamespace(runtime=runtime("other"), tools=[], messages=[])
        assert middleware._recall(request) is request
        assert store.get((*memory._namespace(runtime()), "preferences"), "profile").value == {
            "preferences": ["使用英文"]
        }
    assert len(preference_model.seen) == 2


def test_switches_and_trusted_identity(tmp_path, monkeypatch):
    factory = Mock()
    monkeypatch.setattr(memory, "_manager", factory)
    store = InMemoryStore()
    middleware = memory.UserMemoryMiddleware(settings(tmp_path), store)
    middleware.before_agent(state(), runtime())
    assert store.list_namespaces() == []
    factory.assert_not_called()
    assert memory._namespace(runtime("a.b")) != memory._namespace(runtime("a_b"))
    assert memory._namespace(runtime(tenant="a.b")) != memory._namespace(runtime(tenant="a_b"))
    config = settings(tmp_path, enable_user_preferences=True)
    with pytest.raises(ValueError, match="trusted"):
        memory.UserMemoryMiddleware(config, store).before_agent(state(), runtime(user=""))

    compiled = Mock(return_value=SimpleNamespace(config=None))
    monkeypatch.setattr(compose, "create_deep_agent", compiled)
    monkeypatch.setattr(compose, "init_configured_model", Mock())
    build_agent(settings(tmp_path, enable_memory=False, enable_user_preferences=True), store=store)
    options = compiled.call_args.kwargs
    assert options.get("memory") is None
    assert not any(isinstance(item, memory.UserMemoryMiddleware) for item in options["middleware"])
    assert any(
        item.paths == ["/memories/**"] and item.mode == "deny" for item in options["permissions"]
    )


def test_weekly_due_persistence_idempotency_and_empty_work(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(memory.time, "time", lambda: clock[0])
    manager = Mock()
    manager.invoke.return_value = [
        SimpleNamespace(id="profile", content=memory.ConsolidatedMemory(facts=["项目使用 Python"]))
    ]
    factory = Mock(return_value=manager)
    monkeypatch.setattr(memory, "_manager", factory)
    config = settings(tmp_path, enable_memory_consolidation=True)
    ns = memory._namespace(runtime())
    with open_store(config) as store:
        middleware = memory.UserMemoryMiddleware(config, store)
        middleware.before_agent(state("项目使用 Python"), runtime())
        middleware.before_agent(state("项目使用 Python"), runtime())
        assert len(store.search((*ns, "events"))) == 1
        assert "text" not in store.search((*ns, "events"))[0].value
        assert memory.consolidate_memories(config, store) == 0
        factory.assert_not_called()

    clock[0] += 7 * 86_400
    with open_store(config) as store:
        assert memory.consolidate_memories(config, store) == 1
        assert store.search((*ns, "inbox")) == []
        assert len(store.search((*ns, "events"))) == 1
        assert memory.consolidate_memories(config, store) == 0
        # 重放已处理输入不重新入队。
        memory.UserMemoryMiddleware(config, store).before_agent(state("项目使用 Python"), runtime())
        assert store.search((*ns, "inbox")) == []
        clock[0] += 7 * 86_400
        assert memory.consolidate_memories(config, store) == 0
        assert manager.invoke.call_count == 1


def test_failed_batch_keeps_input_and_resumes_without_repeating_success(tmp_path, monkeypatch):
    config = settings(tmp_path, enable_memory_consolidation=True)
    ns = memory._namespace(runtime())
    result = [SimpleNamespace(id="profile", content=memory.ConsolidatedMemory(facts=["fact"]))]
    manager = Mock()
    manager.invoke.side_effect = [result, RuntimeError("model unavailable"), result]
    monkeypatch.setattr(memory, "_manager", lambda *_: manager)
    with open_store(config) as store:
        memory.UserMemoryMiddleware(config, store).before_agent(state("a" * 40_000), runtime())
        store.put((*ns, "schedule"), "weekly", {"next_due": 0})
        with pytest.raises(RuntimeError, match="1 users"):
            memory.consolidate_memories(config, store)
        assert len(store.search((*ns, "inbox"))) == 1
        assert store.get((*ns, "schedule"), "weekly").value["next_due"] == 0
        assert store.get((*ns, "knowledge"), "profile").value["facts"] == ["fact"]
    with open_store(config) as store:
        assert memory.consolidate_memories(config, store) == 1
        assert store.search((*ns, "inbox")) == []
    assert [len(call.args[0]["messages"]) for call in manager.invoke.call_args_list] == [4, 1, 1]


def test_chunks_keep_original_order_with_equal_timestamps(tmp_path):
    config = settings(tmp_path, enable_memory_consolidation=True)
    store = InMemoryStore()
    memory.UserMemoryMiddleware(config, store).before_agent(
        state("A" * 8000 + "B" * 8000), runtime()
    )
    events = memory._pending_events(store, (*memory._namespace(runtime()), "inbox"))
    assert [event.value["text"][0] for event in events] == ["A", "B"]


def test_preferences_failure_retries_and_replay_is_idempotent(tmp_path, monkeypatch):
    manager = Mock()
    manager.invoke.side_effect = [
        RuntimeError("model unavailable"),
        [SimpleNamespace(id="profile", content=memory.UserPreferences(preferences=["中文"]))],
    ]
    monkeypatch.setattr(memory, "_manager", lambda *_: manager)
    config = settings(tmp_path, enable_user_preferences=True)
    store = InMemoryStore()
    middleware = memory.UserMemoryMiddleware(config, store)
    middleware.before_agent(state(), runtime())
    ns = memory._namespace(runtime())
    assert store.get((*ns, "preferences"), "profile") is None
    assert len(store.search((*ns, "preference_inbox"))) == 1
    middleware.before_agent(state(), runtime())
    middleware.before_agent(state(), runtime())
    assert store.get((*ns, "preferences"), "profile").value == {"preferences": ["中文"]}
    assert manager.invoke.call_count == 2


def test_pagination_and_chronology(tmp_path, monkeypatch):
    store = InMemoryStore()
    manager = Mock(return_value=None)
    manager.invoke.return_value = [
        SimpleNamespace(id="profile", content=memory.ConsolidatedMemory())
    ]
    monkeypatch.setattr(memory, "_manager", lambda *_: manager)
    config = settings(tmp_path, enable_memory_consolidation=True)
    for i in range(101):
        ns = memory._namespace(runtime(user=str(i)))
        store.put((*ns, "inbox"), "new", {"text": "new", "recorded_at": 2})
        store.put((*ns, "inbox"), "old", {"text": "old", "recorded_at": 1})
        store.put((*ns, "schedule"), "weekly", {"next_due": 0})
    assert memory.consolidate_memories(config, store) == 202
    assert all(
        [m.text for m in call.args[0]["messages"]] == ["old", "new"]
        for call in manager.invoke.call_args_list
    )
    ns = memory._namespace(runtime(user="many-events"))
    for i in reversed(range(101)):
        store.put((*ns, "inbox"), str(i), {"text": str(i), "recorded_at": i})
    store.put((*ns, "schedule"), "weekly", {"next_due": 0})
    assert memory.consolidate_memories(config, store) == 101
    assert [len(call.args[0]["messages"]) for call in manager.invoke.call_args_list[-3:]] == [
        50,
        50,
        1,
    ]
    assert manager.invoke.call_args_list[-1].args[0]["messages"][0].text == "100"


def test_invalid_extraction_and_config_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("VA_ENABLE_USER_PREFERENCES", "true")
    monkeypatch.setenv("VA_MEMORY_CONSOLIDATION_INTERVAL_DAYS", "7")
    config = settings(tmp_path)
    assert config.enable_user_preferences
    assert config.memory_consolidation_interval_days == 7
    with pytest.raises(ValidationError):
        settings(tmp_path, memory_consolidation_interval_days=0)
    with pytest.raises(ValidationError):
        memory.ConsolidatedMemory(facts=["x" * 501])
    manager = Mock()
    manager.invoke.return_value = [SimpleNamespace(id="other", content=memory.UserPreferences())]
    with pytest.raises(ValueError, match="single existing profile"):
        memory._extract(manager, memory.UserPreferences, None, ["text"])


def test_cli_worker_and_persistent_store_requirement(tmp_path, monkeypatch):
    config = settings(tmp_path, enable_memory_consolidation=True)
    monkeypatch.setattr(cli, "HarnessSettings", lambda: config)
    worker = Mock()
    monkeypatch.setattr(cli, "run_memory_worker", worker)
    cli.main(["--consolidate-memory"])
    worker.assert_called_once_with(config, once=True)
    cli.main(["--memory-worker"])
    assert worker.call_args.kwargs == {"once": False}
    with pytest.raises(SystemExit):
        cli.main(["--memory-worker", "prompt"])
    for overrides in [{"store_backend": "memory"}, {"store_db_path": ":memory:"}]:
        with pytest.raises(ValueError, match="persistent shared store"):
            memory.run_memory_worker(
                settings(tmp_path, enable_memory_consolidation=True, **overrides)
            )
    with pytest.raises(ValueError, match="requires enabled"):
        memory.run_memory_worker(settings(tmp_path))


def test_worker_once_and_background_retry(tmp_path, monkeypatch):
    config = settings(tmp_path, enable_memory_consolidation=True)
    # 真实打开持久化 Store，空队列不构造模型。
    factory = Mock()
    monkeypatch.setattr(memory, "_manager", factory)
    memory.run_memory_worker(config, once=True)
    factory.assert_not_called()
    process = Mock(side_effect=RuntimeError("temporary failure"))
    monkeypatch.setattr(memory, "consolidate_memories", process)
    with pytest.raises(RuntimeError, match="temporary failure"):
        memory.run_memory_worker(config, once=True)
    sleep = Mock(side_effect=KeyboardInterrupt)
    monkeypatch.setattr(memory.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        memory.run_memory_worker(config)
    sleep.assert_called_once_with(60)
