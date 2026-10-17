"""轻量级交互式运行入口；非生产环境 API 服务。"""

import argparse
import json
import uuid

from vertical_harness.compose import build_agent
from vertical_harness.contracts import RunContext
from vertical_harness.settings import HarnessSettings
from vertical_harness.store import open_store


def main() -> None:
    """列出已启用的插件或使用稳定的线程标识符调用智能体。

    :return: None
    :raises SystemExit: 当未指定输入 prompt 且未传入 --list-plugins 时退出程序。
    """
    # 初始化命令行参数解析器
    parser = argparse.ArgumentParser(description="Deep Agents plugin harness")
    # 支持传入可选的提示词（用户请求内容）
    parser.add_argument("prompt", nargs="?")
    # 支持查看当前已启用插件清单的标志位
    parser.add_argument("--list-plugins", action="store_true")
    # 可选传入持久化会话线程 ID，未指定时默认生成随机 UUID
    parser.add_argument("--thread-id", default=None)
    # 租户隔离标识符，默认 demo
    parser.add_argument("--tenant-id", default="demo")
    # 用户隔离标识符，默认 demo
    parser.add_argument("--user-id", default="demo")
    # 任务层级选择，支持 economy（经济型）和 quality（高品质型）
    parser.add_argument("--task-tier", choices=["economy", "quality"], default="quality")
    args = parser.parse_args()

    # 从环境变量及 .env 文件加载宿主配置
    settings = HarnessSettings()

    # 若指定了 --list-plugins 参数，列出所有已启用的插件清单并退出
    if args.list_plugins:
        from vertical_harness.discovery import discover_plugins

        # 发现并加载所有白名单中的插件清单，以格式化 JSON 输出
        print(
            json.dumps(
                [p.manifest.model_dump() for p in discover_plugins(settings.enabled_plugins)],
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    # 若非列出插件且未指定提示词，则提示错误并退出
    if not args.prompt:
        parser.error("Please specify a prompt")

    # 使用上下文管理器打开持久化存储（如 SQLite Store）
    with open_store(settings) as store:
        # 基于当前配置与存储实例构建 Deep Agent 智能体
        agent = build_agent(settings, store=store)
        # 配置 LangGraph 的 thread_id 用于维持会话状态与检查点恢复
        config = {"configurable": {"thread_id": args.thread_id or str(uuid.uuid4())}}
        # 调用智能体执行交互，注入运行时安全上下文 RunContext
        result = agent.invoke(
            {"messages": [{"role": "user", "content": args.prompt}]},
            config=config,
            context=RunContext(tenant_id=args.tenant_id, user_id=args.user_id, task_tier=args.task_tier),
        )

    # 打印智能体最后生成的回复消息内容
    print(result["messages"][-1].content)

    # 检查智能体执行过程是否触发了审批中断
    if "__interrupt__" in result:
        print("Execution interrupted; resume via LangGraph Command(resume=...) from an approval UI")


if __name__ == "__main__":
    main()


