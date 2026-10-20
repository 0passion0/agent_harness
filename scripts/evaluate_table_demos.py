"""Run isolated, multi-turn desktop-agent evaluations over the table_st demos."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from vertical_harness.config.settings import HarnessSettings
from vertical_harness.desktop_runtime import aopen_desktop_agent, attachment_message, raw_message
from vertical_harness.engine.callbacks import WorkflowLoggingHandler
from vertical_harness.engine.streaming import astream_agent
from vertical_harness.engine.time_travel import make_checkpoint_config
from vertical_plugins.common.contracts import RunContext


class TranscriptCallback(BaseCallbackHandler):
    """Capture actual tool arguments and results from the live callback stream."""

    run_inline = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        self.calls.append({
            "phase": "started", "name": kwargs.get("name") or serialized.get("name"),
            "operation_id": str(kwargs.get("run_id")), "input": input_str,
        })

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        self.calls.append({
            "phase": "completed", "name": kwargs.get("name"),
            "operation_id": str(kwargs.get("run_id")), "output": _jsonable(output),
        })

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        self.calls.append({
            "phase": "failed", "name": kwargs.get("name"),
            "operation_id": str(kwargs.get("run_id")),
            "error": {"type": type(error).__name__, "message": str(error)},
        })


class ConversationBudget(BaseCallbackHandler):
    raise_error = True
    run_inline = True

    def __init__(self) -> None:
        self.model_calls = 0
        self.total_tokens = 0
        self.request_sizes: list[dict[str, int]] = []

    def on_chat_model_start(self, serialized: dict[str, Any], messages: Any, **kwargs: Any) -> None:
        if self.model_calls >= 24 or self.total_tokens >= 300_000:
            raise RuntimeError("Conversation evaluation budget exhausted (24 calls / 300000 reported tokens).")
        self.model_calls += 1
        sizes = [len(message.model_dump_json()) for batch in messages for message in batch]
        self.request_sizes.append({"messages": len(sizes), "characters": sum(sizes), "largest_message": max(sizes)})

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        usage = response.generations[0][0].message.usage_metadata
        if usage is None:
            raise RuntimeError("Model did not report usage; stopping the paid evaluation.")
        self.total_tokens += usage["total_tokens"]


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    return str(value)


def _input_files(root: Path, demo: int) -> tuple[Path, Path]:
    folder = root / f"demo{demo}"
    workbooks = sorted(folder.glob("*.xlsx"))
    prompts = sorted(folder.glob("*.md"))
    if len(workbooks) != 1 or len(prompts) != 1:
        raise ValueError(f"demo{demo} requires exactly one .xlsx and one .md in {folder}")
    return workbooks[0], prompts[0]


def _turn_prompts(original: str, demo: int) -> list[str]:
    if demo == 1:
        headings = [
            "主分析",
            "补充分析一：30 天死亡",
            "补充分析二：炎症指标变化",
            "补充分析三：住院时长",
            "补充分析四：抗菌药物策略的观察性比较",
            "补充分析五：入院生物标志物关系",
        ]
        sections: list[str] = []
        for heading in headings:
            marker = f"## {heading}"
            start = original.index(marker)
            end = min(
                (original.index(f"## {next_heading}") for next_heading in headings if original.index(f"## {next_heading}") > start),
                default=len(original),
            )
            sections.append(original[start:end].strip())

        preamble = original[:original.index("## 主分析")].strip()
        data_requirements = original[original.index("## 数据处理要求"):].strip()
        one_method = "本轮单独确认并执行这一项分析，不得把其他方向合并进本轮的统计运算。不得补造数据、口径或结果。"
        supplement_prompts = [f"{one_method}\n\n{section}" for section in sections[1:]]
        return [
            f"{preamble}\n\n{one_method}\n\n{sections[0]}\n\n{data_requirements}",
            *supplement_prompts,
            "请只核对前六轮已经生成的文件和结果是否覆盖了原需求中的主分析及补充分析一至五，检查计算依据、报告结论和限制是否与原始数据及各轮要求一致。列出已完成和缺失的交付物及证据；不要运行新的分析、重新拟合模型或生成新的统计结果。",
        ]

    sequencing = ""
    return [
        original,
        f"请继续完成原需求中所有可执行的分析与交付项，不要缩减原需求范围。可以执行必要的数据处理，但不得补造缺失值、假设、业务口径或未提供的数据；遇到这些情况请明确说明。{sequencing}继续使用现有文件和本会话工作区。",
        f"请核对并完成原需求中尚未完成的可执行分析和交付项：检查已有结果是否有数据和计算依据，补齐仍缺少且可由原始数据计算的内容。最后列出实际生成的文件，并简短审计结果依据、缺失信息及未完成事项；不要声称未实际完成的工作。{sequencing}",
    ]


def _interrupt_answer(value: Any, turn: int) -> Any:
    actions = value.get("action_requests", []) if isinstance(value, dict) else []
    if actions:
        return {"decisions": [{"type": "approve"} for _ in actions]}
    if isinstance(value, dict) and value.get("kind") == "plugin_authorization":
        return {"request_id": value["request_id"], "approved": True}
    if isinstance(value, dict) and value.get("kind") == "approval":
        return {"decisions": [{"type": "approve"}]}
    question = value.get("question", "") if isinstance(value, dict) else str(value)
    options = value.get("options", []) if isinstance(value, dict) else []
    q = f"{question} {' '.join(map(str, options))}".lower()
    if re.search(r"(sheet|工作表|表名|工作表名)", q):
        return "请先查看工作簿并使用与数据对应的工作表；若只有一个工作表就使用该表。"
    if re.search(r"(date|日期|时间范围|时间段)", q):
        return "使用文件中实际存在的日期或时间字段；不要推断或补造范围。"
    if re.search(r"(missing|null|缺失|空值)", q):
        return "先报告实际缺失情况；只有在原始数据能支持时才处理，不要擅自填补。"
    if options:
        return "请根据原始文件中可验证的信息选择；如仍无法确定，请说明限制，不要猜测。"
    return "请依据输入文件中可验证的信息继续；若缺少必要口径或值，请指出并停止该部分，不要猜测。"


def _desktop_stream_config(
    agent: Any, thread_id: str, turn_id: str, context: RunContext,
    selected_plugins: list[str], selected_skills: list[str], callbacks: list[Any],
) -> dict[str, Any]:
    config = make_checkpoint_config(thread_id)
    config["metadata"] = {
        "desktop_run_id": turn_id,
        "desktop_selected_plugins": json.dumps(sorted(selected_plugins)),
        "desktop_selected_skills": json.dumps(sorted(selected_skills)),
        "desktop_tenant_id": context.tenant_id,
        "desktop_user_id": context.user_id,
    }
    config.update(
        run_id=turn_id,
        recursion_limit=agent.config["recursion_limit"],
        callbacks=callbacks,
    )
    return config


def _check_result(text: str, events: list[dict[str, Any]], tools: list[dict[str, Any]], workspace: Path) -> dict[str, Any]:
    low = text.lower()
    generated = [
        p for p in workspace.rglob("*")
        if p.is_file()
        and p.name not in {"request.md", "source.xlsx", "transcript.json", "progress.json", "status.json", "checkpoint-messages.json"}
        and not any(part.startswith(".") or "checkpoint" in part.lower() or "sqlite" in part.lower() or "runtime" in part.lower() or part.lower() == "store" for part in p.relative_to(workspace).parts)
        and not p.name.endswith((".sqlite", ".sqlite-shm", ".sqlite-wal"))
    ]
    names = [p.relative_to(workspace).as_posix() for p in generated]
    tool_names = [str(call.get("name") or "") for call in tools]
    evidence = any(name in {"execute_python_file", "execute", "write_file", "edit_file"} for name in tool_names)
    errors = [e for e in events if e["type"] in {"run.failed", "tool.failed"}]
    errors.extend({"type": "tool.failed", "data": call.get("error")} for call in tools if call.get("phase") == "failed")
    refusal = bool(re.search(r"无法(?:完成|分析|处理)|不能(?:完成|访问|分析)|i can(?:not|'t) (?:complete|analyze|access)", low))
    fabricated = bool(re.search(r"已(?:生成|保存|完成).{0,20}(?:文件|报告|图表)|successfully (?:created|saved|generated)", low)) and not names
    numerical_content = bool(re.search(r"\d+(?:\.\d+)?\s*(?:%|个百分点|个|次|元|天|小时|分钟|条|行|列)?", text))
    final_language = bool(text.strip()) and len(text.strip()) >= 40
    budget_exhausted = "model call limits exceeded" in low
    return {
        "artifact_names": names,
        "artifact_hashes": {name: hashlib.sha256((workspace / name).read_bytes()).hexdigest() for name in names},
        "tool_evidence": evidence,
        "tool_names": tool_names,
        "unsupported_method_or_refusal": refusal,
        "fabricated_completion": fabricated,
        "numerical_deliverable_present": numerical_content,
        "final_language_present": final_language,
        "model_budget_exhausted": budget_exhausted,
        "execution_errors": errors,
        "scope_review": "pending_human_review",
        "needs_review": bool(budget_exhausted or refusal or fabricated or errors or not evidence or not names or not numerical_content or not final_language),
        "final_text": text,
    }


async def _run_demo(root: Path, output: Path, demo: int, phase: str, semaphore: asyncio.Semaphore,
                    turn_timeout: float) -> dict[str, Any]:
    async with semaphore:
        started = time.monotonic()
        work = output / f"demo{demo}"
        work.mkdir()
        status_path = work / "status.json"
        status_path.write_text(json.dumps({"demo": demo, "phase": phase, "status": "running", "updated_at": datetime.now(UTC).isoformat()}), encoding="utf-8")
        workbook, prompt_path = _input_files(root, demo)
        (work / "source.xlsx").write_bytes(workbook.read_bytes())
        original = prompt_path.read_text(encoding="utf-8-sig")
        (work / "request.md").write_text(original, encoding="utf-8")
        runtime = work / ".private-runtime"
        runtime.mkdir()
        settings = HarnessSettings(
            workspace_root=work, runtime_data_dir=runtime, store_db_path=runtime / "store.sqlite",
            checkpoint_db_path=runtime / "checkpoints.sqlite", enable_memory=False, enable_user_preferences=False,
            enable_memory_consolidation=False, enabled_plugins=["data_analysis"],
            run_timeout_seconds=turn_timeout,
        )
        options = {
            "model_code": "", "runtimes": {"python": str(Path.cwd() / ".venv/Scripts/python.exe"), "node": "node"},
            "permission_mode": "full", "max_context_tokens": 475000, "agent_content": "",
            "skills": [], "mcp_servers": {}, "enabled_plugins": ["data_analysis"],
        }
        thread_id = f"table-demo-{phase}-{demo}-{uuid4().hex}"
        context = RunContext(tenant_id="table-demo-eval", user_id=f"demo-{demo}")
        turns: list[dict[str, Any]] = []
        final_text = ""
        pending: Any = None
        selected_plugins = ["data_analysis"]
        selected_skills: list[str] = []
        budget = ConversationBudget()
        async with aopen_desktop_agent(
            settings, options, selected_skill_ids=frozenset(selected_skills),
            selected_plugin_ids=frozenset(selected_plugins),
        ) as agent:
            for index, prompt in enumerate(_turn_prompts(original, demo), 1):
                logger = WorkflowLoggingHandler()
                capture = TranscriptCallback()
                events: list[dict[str, Any]] = []
                answers_used: list[dict[str, str]] = []
                turn_id = str(uuid4())
                config = _desktop_stream_config(
                    agent, thread_id, turn_id, context, selected_plugins, selected_skills,
                    [budget, logger, capture],
                )
                if index == 1:
                    msg = attachment_message(work, prompt, [{"name": workbook.name, "path": str(work / "source.xlsx"), "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}])
                    inputs: Any = {"messages": [HumanMessage(content=msg)]}
                elif pending is not None:
                    answers = {item["id"]: _interrupt_answer(item["value"], index) for item in pending}
                    answers_used.append(answers)
                    inputs = Command(resume=answers)
                else:
                    inputs = {"messages": [HumanMessage(content=prompt)]}
                turn_started = time.monotonic()
                progress_file = work / "progress.json"
                progress_file.write_text(json.dumps({"demo": demo, "turn": index, "status": "running", "updated_at": datetime.now(UTC).isoformat(), "events": events}, ensure_ascii=False, indent=2), encoding="utf-8")
                try:
                    while True:
                        pending = None
                        async for event in astream_agent(
                            agent, inputs, config=config, context=context,
                            durability=settings.durability, timeout_seconds=turn_timeout,
                        ):
                            events.append(event.model_dump(mode="json"))
                            if event.type in {"model.completed", "tool.completed", "run.interrupted", "run.completed", "run.failed"}:
                                progress_file.write_text(json.dumps({"demo": demo, "turn": index, "status": "running", "updated_at": datetime.now(UTC).isoformat(), "events": events}, ensure_ascii=False, indent=2), encoding="utf-8")
                            if event.type == "run.interrupted":
                                pending = event.data.get("interrupts", [])
                            elif event.type == "run.completed":
                                final_text = str(event.data.get("output", ""))
                        if not pending:
                            break
                        answers = {item["id"]: _interrupt_answer(item["value"], index) for item in pending}
                        answers_used.append(answers)
                        inputs = Command(resume=answers)
                except Exception as exc:
                    turns.append({"turn": index, "prompt": prompt, "answers": answers_used, "error": {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}, "events": events, "tools": capture.calls, "usage": logger.usage, "duration_seconds": round(time.monotonic()-turn_started, 3)})
                    progress_file.write_text(json.dumps({"demo": demo, "turn": index, "status": "failed", "updated_at": datetime.now(UTC).isoformat(), "events": events}, ensure_ascii=False, indent=2), encoding="utf-8")
                    break
                turns.append({"turn": index, "prompt": prompt, "answers": answers_used, "events": events, "tools": capture.calls, "usage": logger.usage, "duration_seconds": round(time.monotonic()-turn_started, 3)})
                (runtime / f"turn-{index}.json").write_text(json.dumps(turns[-1], ensure_ascii=False, indent=2), encoding="utf-8")
                progress_file.write_text(json.dumps({"demo": demo, "turn": index, "status": "completed", "updated_at": datetime.now(UTC).isoformat(), "events": events, "tools": capture.calls, "usage": logger.usage}, ensure_ascii=False, indent=2), encoding="utf-8")
            snapshot = await agent.aget_state({"configurable": {"thread_id": thread_id}})
            checkpoint_messages = [raw_message(message) for message in snapshot.values.get("messages", [])]
            (work / "checkpoint-messages.json").write_text(json.dumps(checkpoint_messages, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        all_events = [event for turn in turns for event in turn.get("events", [])]
        all_tools = [call for turn in turns for call in turn.get("tools", [])]
        all_errors = [turn["error"] for turn in turns if "error" in turn]
        checks = _check_result(final_text, all_events, all_tools, work)
        checks["turn_errors"] = all_errors
        checks["needs_review"] = checks["needs_review"] or bool(all_errors)
        result = {"demo": demo, "phase": phase, "thread_id": thread_id, "source_workbook": workbook.name, "source_prompt": prompt_path.name, "turns": turns, "checkpoint_message_count": len(checkpoint_messages), "checks": checks, "duration_seconds": round(time.monotonic()-started, 3)}
        result["usage"] = _aggregate_usage(turns)
        result["conversation_budget"] = {"model_calls": budget.model_calls, "reported_tokens": budget.total_tokens,
                                         "request_sizes": budget.request_sizes}
        (work / "transcript.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        status_path.write_text(json.dumps({"demo": demo, "phase": phase, "status": "failed" if all_errors else "completed", "updated_at": datetime.now(UTC).isoformat(), "turns_completed": sum("error" not in turn for turn in turns), "needs_review": checks["needs_review"]}), encoding="utf-8")
        return result


def _aggregate_usage(turns: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {"model_calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
              "cache_read_tokens": 0, "cache_creation_tokens": 0, "reasoning_tokens": 0,
              "usage_reported_calls": 0, "complete": True, "duration_seconds": 0.0}
    for turn in turns:
        usage = turn.get("usage", {})
        totals["model_calls"] += usage.get("model_calls", 0)
        totals["usage_reported_calls"] += usage.get("usage_reported_calls", 0)
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            totals[key] += usage.get(key, 0)
        details_in = usage.get("input_token_details", {})
        details_out = usage.get("output_token_details", {})
        totals["cache_read_tokens"] += details_in.get("cache_read", 0) + details_in.get("cache_read_input_tokens", 0)
        totals["cache_creation_tokens"] += details_in.get("cache_creation", 0) + details_in.get("cache_creation_input_tokens", 0)
        totals["reasoning_tokens"] += details_out.get("reasoning", 0) + details_out.get("reasoning_tokens", 0)
        totals["complete"] = totals["complete"] and usage.get("complete", False)
        totals["duration_seconds"] += turn.get("duration_seconds", 0)
    return totals


async def _main(args: argparse.Namespace) -> int:
    if len(args.demos) != 1 or args.concurrency != 1:
        raise ValueError("Evaluate exactly one demo at a time, with concurrency=1.")
    root = args.demo_root.resolve(strict=True)
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"output directory already exists: {output}")
    output.mkdir(parents=True)
    os.environ["LANGSMITH_TRACING"] = "false"
    demos = args.demos
    semaphore = asyncio.Semaphore(args.concurrency)
    tasks = [_run_demo(root, output, demo, args.phase, semaphore, args.turn_timeout) for demo in demos]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    rows = []
    for demo, result in zip(demos, results, strict=True):
        if isinstance(result, BaseException):
            status_path = output / f"demo{demo}" / "status.json"
            if status_path.parent.exists():
                status_path.write_text(json.dumps({"demo": demo, "phase": args.phase, "status": "failed", "updated_at": datetime.now(UTC).isoformat(), "error": {"type": type(result).__name__, "message": str(result)}}), encoding="utf-8")
            rows.append({"demo": demo, "phase": args.phase, "error": {"type": type(result).__name__, "message": str(result), "traceback": ""}, "needs_review": True})
        else:
            rows.append(result)
    manifest = {"phase": args.phase, "created_at": datetime.now(UTC).isoformat(), "results": rows}
    (output / "results.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output / "results.json")
    return int(any(
        "error" in row or row["checks"]["turn_errors"] or row["checks"]["model_budget_exhausted"]
        for row in rows
    ))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--demos", type=int, nargs=1, required=True, choices=range(1, 22))
    parser.add_argument("--concurrency", type=int, default=1, choices=[1])
    parser.add_argument("--turn-timeout", type=float, default=300)
    args = parser.parse_args()
    if not sys.flags.utf8_mode:
        parser.error("Use Python -X utf8, matching the desktop backend's UTF-8 process mode.")
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
