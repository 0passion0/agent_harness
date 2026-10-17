"""Minimal interactive runner; not a production API server."""

import argparse
import json
import uuid

from vertical_harness.compose import build_agent
from vertical_harness.contracts import RunContext
from vertical_harness.settings import HarnessSettings


def main() -> None:
    """List plugins or invoke the agent with a stable thread identifier."""
    parser = argparse.ArgumentParser(description="Deep Agents plugin harness")
    parser.add_argument("prompt", nargs="?")
    parser.add_argument("--list-plugins", action="store_true")
    parser.add_argument("--thread-id", default=None)
    parser.add_argument("--tenant-id", default="demo")
    parser.add_argument("--user-id", default="demo")
    parser.add_argument("--task-tier", choices=["economy", "quality"], default="quality")
    args = parser.parse_args()

    settings = HarnessSettings()
    if args.list_plugins:
        from vertical_harness.discovery import discover_plugins

        print(json.dumps([p.manifest.model_dump() for p in discover_plugins(settings.enabled_plugins)], ensure_ascii=False, indent=2))
        return
    if not args.prompt:
        parser.error("Please specify a prompt")
    agent = build_agent(settings)
    config = {"configurable": {"thread_id": args.thread_id or str(uuid.uuid4())}}
    result = agent.invoke(
        {"messages": [{"role": "user", "content": args.prompt}]},
        config=config,
        context=RunContext(tenant_id=args.tenant_id, user_id=args.user_id, task_tier=args.task_tier),
    )
    print(result["messages"][-1].content)
    if "__interrupt__" in result:
        print("Execution interrupted; resume via LangGraph Command(resume=...) from an approval UI")


if __name__ == "__main__":
    main()
