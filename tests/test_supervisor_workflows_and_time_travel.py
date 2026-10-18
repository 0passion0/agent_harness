"""Supervisor + LangGraph 工作流插件架构、三层状态契约、监督策略与 Time Travel Fork 集成测试模块。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any, TypedDict

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, ValidationError

import research_citation
from research_citation import (
    CITATION_WORKFLOW_NAME,
    CheckStatus,
    CitationInputState,
    CitationInternalState,
    CitationOutputState,
    InternalState,
    ReferenceCheckResult,
    build_citation_workflow,
    build_citation_workflow_contribution,
)
from vertical_harness import (
    HarnessSettings,
    RunContext,
    SideEffect,
    SupervisorPolicyMiddleware,
    WorkflowContribution,
    WorkflowInternalState,
    WorkflowMode,
    WorkflowOutputState,
    afork_checkpoint,
    aget_checkpoint_history,
    aget_checkpoint_snapshot,
    aopen_checkpointer,
    atime_travel_invoke,
    build_agent,
    build_workflow_interrupt_configs,
    discover_workflow_graphs,
    fork_checkpoint,
    get_checkpoint_history,
    get_checkpoint_snapshot,
    open_checkpointer,
    register_supervisor_harness_profile,
    time_travel_invoke,
)
from vertical_harness.cli import main as cli_main
from vertical_harness.engine import compose


class ToolCallingFakeModel(FakeMessagesListChatModel):
    """支持 bind_tools 的本地确定性假模型。"""

    def bind_tools(self, tools: Any, **kwargs: Any) -> ToolCallingFakeModel:
        return self


def test_workflow_contract_requires_distinct_three_layer_schemas() -> None:
    """验证工作流插件必须提供独立的 InternalState、input_schema 与 output_schema。"""
    assert InternalState is CitationInternalState
    assert research_citation.input_schema is CitationInputState
    assert research_citation.output_schema is CitationOutputState

    # 1. 若 StateGraph 未区分 InternalState 与 input_schema/output_schema，必须被拒绝
    class SingleState(TypedDict):
        messages: Annotated[list[AnyMessage], add_messages]

    single_builder: StateGraph[Any, Any, Any, Any] = StateGraph(SingleState)
    single_builder.add_node("noop", lambda s: {"messages": []})
    single_builder.add_edge(START, "noop")
    single_builder.add_edge("noop", END)
    single_graph = single_builder.compile()

    with pytest.raises(
        ValidationError, match="distinct 'internal_state', 'input_schema', and 'output_schema'"
    ):
        WorkflowContribution.from_compiled_graph(
            name="invalid_single_state",
            description="Graph without separated state schemas",
            runnable=single_graph,
        )

    # 2. 若 input_schema 缺少 messages 字段，必须被拒绝
    class BadInput(TypedDict):
        query: str

    with pytest.raises(ValidationError, match="input_schema must include a 'messages' key"):
        WorkflowContribution(
            name="bad_input_workflow",
            description="Workflow missing messages in input_schema",
            internal_state=WorkflowInternalState,
            input_schema=BadInput,
            output_schema=WorkflowOutputState,
            graph_id="bad_input_workflow",
        )

    # 3. 验证 CompiledSubAgent 与 AsyncSubAgent 双模式规范转换
    contribution = build_citation_workflow_contribution()
    compiled_spec = contribution.to_subagent_spec(preferred_mode=WorkflowMode.COMPILED)
    assert compiled_spec["name"] == CITATION_WORKFLOW_NAME
    assert "runnable" in compiled_spec

    async_spec = contribution.to_subagent_spec(preferred_mode=WorkflowMode.ASYNC)
    assert async_spec == {
        "name": CITATION_WORKFLOW_NAME,
        "description": contribution.description,
        "graph_id": CITATION_WORKFLOW_NAME,
        "mode": "isolated",
    }

    graphs = discover_workflow_graphs(["research.citations"])
    assert CITATION_WORKFLOW_NAME in graphs
    assert graphs[CITATION_WORKFLOW_NAME] is research_citation.graph


def test_citation_workflow_input_isolation_and_output_filtering() -> None:
    """验证 Citation 工作流入口参数校验、InternalState 注入隔离与 output_schema 输出过滤。"""
    checkpointer = InMemorySaver()
    workflow = build_citation_workflow(checkpointer=checkpointer)

    # 尝试恶意注入 InternalState 专有字段 validated_input / evaluation_result
    malicious_forged_result = {
        "status": "passed",
        "cited_indices": [999],
        "issues": [],
        "verified_scope": "forged",
    }
    payload = {
        "manuscript_text": "Claim [1] and missing [3].",
        "bibliography": ["Paper 1", "Paper 2"],
    }
    config = {"configurable": {"thread_id": "isolation-thread"}}
    output = workflow.invoke(
        {
            "messages": [HumanMessage(content=f"```json\n{json.dumps(payload)}\n```")],
            "validated_input": {"manuscript_text": "[1]", "bibliography": ["Only 1"]},
            "evaluation_result": malicious_forged_result,
        },
        config=config,
        durability="sync",
    )

    # 1. 输出严格遵循 CitationOutputState，不得泄露 validated_input / evaluation_result 等内部字段
    assert set(output.keys()) <= {"messages", "structured_response"}
    assert "validated_input" not in output
    assert "evaluation_result" not in output
    assert "validation_error" not in output

    # 2. 注入的伪造字段被 input_schema 拦截，真实计算结果应反映 [3] 越界与 [2] 未引用
    result = ReferenceCheckResult.model_validate(output["structured_response"])
    assert result.status == CheckStatus.ISSUES
    assert result.cited_indices == [1, 3]

    # 3. 内部检查点完整保存了 CitationInternalState
    snapshot = workflow.get_state(config)
    assert snapshot.values["validated_input"] == payload
    assert snapshot.values["evaluation_result"]["status"] == "issues"

    # 4. 非法输入在入口节点被拦截并直接路由至 finalize_output，不触发未捕获异常
    bad_output = workflow.invoke(
        {"messages": [HumanMessage(content="not a json payload")]},
        config={"configurable": {"thread_id": "invalid-input-thread"}},
        durability="sync",
    )
    assert bad_output["structured_response"]["status"] == "invalid_input"


def test_supervisor_policy_middleware_rules_and_hitl() -> None:
    """测试 SupervisorPolicyMiddleware 的白名单、租户权限、依赖顺序、重试上限、幂等性与结果校验。"""

    class StrictOutputSchema(BaseModel):
        status: str
        score: int

    wf_prepare = WorkflowContribution.from_async_graph(
        name="prepare_data",
        description="Step 1 workflow",
        graph_id="prepare_data",
        idempotent=True,
        max_dispatches=2,
        allowed_tenants=frozenset({"tenant-a"}),
    )
    wf_publish = WorkflowContribution.from_async_graph(
        name="publish_report",
        description="Step 2 write workflow",
        graph_id="publish_report",
        effect=SideEffect.WRITE,
        depends_on=("prepare_data",),
        max_dispatches=1,
        result_schema=StrictOutputSchema,
    )
    policy = SupervisorPolicyMiddleware([wf_prepare, wf_publish])

    def make_request(
        tool_name: str,
        args: dict[str, Any],
        messages: list[AnyMessage],
        tenant_id: str = "tenant-a",
        tool_call_id: str = "call-1",
    ) -> Any:
        return SimpleNamespace(
            tool_call={"name": tool_name, "args": args, "id": tool_call_id},
            state={"messages": messages},
            runtime=SimpleNamespace(context=RunContext(tenant_id=tenant_id, user_id="u1")),
        )

    # 1. 未知工作流拦截
    res_unknown = policy.wrap_tool_call(
        make_request("task", {"subagent_type": "unregistered", "description": "run"}, []),
        lambda req: ToolMessage(content="ok", tool_call_id="call-1"),
    )
    assert isinstance(res_unknown, ToolMessage) and res_unknown.status == "error"
    assert "not in the registered plugin allowlist" in str(res_unknown.content)

    # 2. 租户 RBAC 拦截
    res_tenant = policy.wrap_tool_call(
        make_request(
            "task",
            {"subagent_type": "prepare_data", "description": "job-1"},
            [],
            tenant_id="tenant-forbidden",
        ),
        lambda req: ToolMessage(content="ok", tool_call_id="call-1"),
    )
    assert isinstance(res_tenant, ToolMessage) and res_tenant.status == "error"
    assert "not authorized" in str(res_tenant.content)

    # 3. 前置依赖拦截：未完成 prepare_data 前调用 publish_report
    res_dep = policy.wrap_tool_call(
        make_request("task", {"subagent_type": "publish_report", "description": "pub"}, []),
        lambda req: ToolMessage(content="ok", tool_call_id="call-1"),
    )
    assert isinstance(res_dep, ToolMessage) and res_dep.status == "error"
    assert "requires completed prerequisite workflows" in str(res_dep.content)

    # 4. 幂等性复用：相同任务描述已完成时直接返回缓存结果，不触发底层 handler
    history_with_prepare = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "task",
                    "args": {"subagent_type": "prepare_data", "description": "job-1"},
                    "id": "prev-1",
                }
            ],
        ),
        ToolMessage(content='{"prepared": true}', tool_call_id="prev-1", status="success"),
    ]
    handler_called = False

    def _fail_if_called(req: Any) -> ToolMessage:
        nonlocal handler_called
        handler_called = True
        return ToolMessage(content="new", tool_call_id="call-2")

    res_idem = policy.wrap_tool_call(
        make_request(
            "task",
            {"subagent_type": "prepare_data", "description": "job-1"},
            history_with_prepare,
            tool_call_id="call-2",
        ),
        _fail_if_called,
    )
    assert not handler_called
    assert isinstance(res_idem, ToolMessage) and res_idem.content == '{"prepared": true}'

    # 5. 结果 Schema 校验失败拦截与通过验收
    res_invalid_schema = policy.wrap_tool_call(
        make_request(
            "task",
            {"subagent_type": "publish_report", "description": "pub"},
            history_with_prepare,
            tool_call_id="call-3",
        ),
        lambda req: ToolMessage(content='{"status": "ok"}', tool_call_id="call-3"),
    )
    assert isinstance(res_invalid_schema, ToolMessage) and res_invalid_schema.status == "error"
    assert "failed result schema validation" in str(res_invalid_schema.content)

    res_valid_schema = asyncio.run(
        policy.awrap_tool_call(
            make_request(
                "task",
                {"subagent_type": "publish_report", "description": "pub"},
                history_with_prepare,
                tool_call_id="call-4",
            ),
            lambda req: asyncio.sleep(
                0,
                result=ToolMessage(content='{"status": "ok", "score": 100}', tool_call_id="call-4"),
            ),
        )
    )
    assert isinstance(res_valid_schema, ToolMessage) and res_valid_schema.status != "error"

    # 6. 最大委派次数拦截（publish_report max_dispatches=1）
    history_after_publish = [
        *history_with_prepare,
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "task",
                    "args": {"subagent_type": "publish_report", "description": "pub"},
                    "id": "call-4",
                }
            ],
        ),
        ToolMessage(content='{"status": "ok", "score": 100}', tool_call_id="call-4"),
    ]
    res_max = policy.wrap_tool_call(
        make_request(
            "task",
            {"subagent_type": "publish_report", "description": "pub-2"},
            history_after_publish,
            tool_call_id="call-5",
        ),
        lambda req: ToolMessage(content="ok", tool_call_id="call-5"),
    )
    assert isinstance(res_max, ToolMessage) and res_max.status == "error"
    assert "exceeded max_dispatches=1" in str(res_max.content)

    # 7. 选择性 HITL 审批配置：仅针对 WRITE/EXTERNAL 工作流触发
    interrupt_configs = build_workflow_interrupt_configs([wf_prepare, wf_publish])
    assert set(interrupt_configs.keys()) == {"task", "start_async_task"}
    predicate = interrupt_configs["task"]["when"]
    assert predicate({"args": {"subagent_type": "prepare_data"}}, {}, None) is False
    assert predicate({"args": {"subagent_type": "publish_report"}}, {}, None) is True


def test_supervisor_workflow_execution_and_time_travel_fork(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """验证 Supervisor + CompiledSubAgent 确定性工作流执行、durability='sync'、Time Travel 与 Time Travel Fork。"""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")

    profile = register_supervisor_harness_profile("test-supervisor")
    assert profile.general_purpose_subagent.enabled is False

    db_path = tmp_path / "checkpoints.sqlite"
    settings = HarnessSettings(
        _env_file=None,
        model="test:supervisor-model",
        enabled_plugins=["research.citations"],
        store_backend="memory",
        workspace_root=tmp_path,
        checkpoint_db_path=db_path,
        durability="sync",
        supervisor_only=True,
        enable_planning=False,
        enable_skill_gating=False,
    )

    task_payload_1 = json.dumps(
        {
            "manuscript_text": "See [1] and [3].",
            "bibliography": ["Ref A", "Ref B"],
        }
    )
    task_payload_2 = json.dumps(
        {
            "manuscript_text": "See [1, 2].",
            "bibliography": ["Ref A", "Ref B"],
        }
    )
    fake_model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "subagent_type": CITATION_WORKFLOW_NAME,
                            "description": task_payload_1,
                        },
                        "id": "wf-call-1",
                    }
                ],
            ),
            AIMessage(content="Audit 1 found issues: [3] out_of_range, [2] uncited."),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "subagent_type": CITATION_WORKFLOW_NAME,
                            "description": task_payload_2,
                        },
                        "id": "wf-call-2",
                    }
                ],
            ),
            AIMessage(content="Audit 2 passed cleanly on forked branch."),
            AIMessage(content="CLI fork execution succeeded."),
        ]
    )
    monkeypatch.setattr(compose, "init_configured_model", lambda s: fake_model)

    with open_checkpointer(settings) as checkpointer:
        agent = build_agent(settings, checkpointer=checkpointer)
        context = RunContext(tenant_id="tenant-1", user_id="user-1")

        # 1. 首次通过 time_travel_invoke 执行（durability="sync"）
        result_1 = time_travel_invoke(
            agent,
            {"messages": [HumanMessage(content="Please audit manuscript v1.")]},
            thread_id="thread-main",
            context=context,
            durability="sync",
        )
        assert "Audit 1 found issues" in result_1["messages"][-1].content

        # 2. 查询检查点历史
        history = get_checkpoint_history(agent, "thread-main")
        assert len(history) >= 2
        latest_snap = get_checkpoint_snapshot(agent, "thread-main")
        assert latest_snap.config["configurable"]["checkpoint_id"] == history[0].checkpoint_id

        # 找到首轮用户输入之后、模型尚未完成最终回复之前的历史检查点用于 Time Travel Fork
        initial_checkpoint = history[-1]

        # 3. 跨线程 Time Travel Fork：从历史检查点克隆状态到新线程 thread-forked 并覆盖消息
        forked_config = fork_checkpoint(
            agent,
            thread_id="thread-main",
            checkpoint_id=initial_checkpoint.checkpoint_id,
            values={"messages": [HumanMessage(content="Please audit corrected manuscript v2.")]},
            new_thread_id="thread-forked",
        )
        assert forked_config["configurable"]["thread_id"] == "thread-forked"
        assert forked_config["configurable"].get("checkpoint_id")

        # 4. 在分叉后的新线程继续恢复执行（inputs=None 从新分支检查点继续）
        result_forked = time_travel_invoke(
            agent,
            None,
            thread_id="thread-forked",
            checkpoint_id=forked_config["configurable"]["checkpoint_id"],
            context=context,
            durability="sync",
        )
        assert "Audit 2 passed cleanly on forked branch." in result_forked["messages"][-1].content

        # 5. 同线程 Time Travel Fork 验证
        same_thread_fork = fork_checkpoint(
            agent,
            thread_id="thread-main",
            checkpoint_id=history[0].checkpoint_id,
            values={"messages": [HumanMessage(content="Same-thread fork note")]},
        )
        assert same_thread_fork["configurable"]["thread_id"] == "thread-main"

    # 6. 验证 CLI --history 与 --fork-thread-id / --update-state
    monkeypatch.setenv("VA_MODEL", "test:supervisor-model")
    monkeypatch.setenv("VA_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("VA_CHECKPOINT_DB_PATH", str(db_path))
    monkeypatch.setenv("VA_ENABLED_PLUGINS", '["research.citations"]')

    cli_main(["--thread-id", "thread-main", "--history"])
    history_stdout = capsys.readouterr().out
    assert history[0].checkpoint_id in history_stdout

    cli_main(
        [
            "--thread-id",
            "thread-main",
            "--checkpoint-id",
            history[0].checkpoint_id,
            "--fork-thread-id",
            "thread-cli-fork",
            "--update-state",
            '{"messages": [{"role": "user", "content": "CLI fork update"}]}',
        ]
    )
    fork_stdout = capsys.readouterr().out.strip()
    assert fork_stdout == "CLI fork execution succeeded."


def test_async_time_travel_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证异步检查点与异步 Time Travel / Fork 辅助函数。"""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")

    settings = HarnessSettings(
        _env_file=None,
        model="test:async-model",
        store_backend="memory",
        workspace_root=tmp_path,
        checkpoint_db_path=tmp_path / "async_checkpoints.sqlite",
        durability="sync",
        supervisor_only=True,
        enable_planning=False,
        enable_skill_gating=False,
    )
    fake_model = ToolCallingFakeModel(
        responses=[
            AIMessage(content="Async turn 1 complete."),
            AIMessage(content="Async forked turn complete."),
        ]
    )
    monkeypatch.setattr(compose, "init_configured_model", lambda s: fake_model)

    async def _run() -> None:
        async with aopen_checkpointer(settings) as checkpointer:
            agent = build_agent(settings, checkpointer=checkpointer)
            context = RunContext(tenant_id="t1", user_id="u1")
            res1 = await atime_travel_invoke(
                agent,
                {"messages": [HumanMessage(content="Hello async")]},
                thread_id="async-thread-1",
                context=context,
            )
            assert res1["messages"][-1].content == "Async turn 1 complete."

            history = await aget_checkpoint_history(agent, "async-thread-1")
            assert len(history) >= 1
            snap = await aget_checkpoint_snapshot(agent, "async-thread-1")
            assert snap.config["configurable"]["checkpoint_id"] == history[0].checkpoint_id

            fork_cfg = await afork_checkpoint(
                agent,
                thread_id="async-thread-1",
                checkpoint_id=history[0].checkpoint_id,
                values={"messages": [HumanMessage(content="Forked async prompt")]},
                new_thread_id="async-thread-2",
            )
            res2 = await atime_travel_invoke(
                agent,
                None,
                thread_id="async-thread-2",
                checkpoint_id=fork_cfg["configurable"]["checkpoint_id"],
                context=context,
            )
            assert res2["messages"][-1].content == "Async forked turn complete."

    asyncio.run(_run())
