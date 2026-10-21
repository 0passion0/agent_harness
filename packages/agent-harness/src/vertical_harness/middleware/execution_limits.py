"""智能体任务最大执行步数限制与 Token 输出超限自动续跑中间件模块。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

MAX_STEPS_PROMPT = """CRITICAL - MAXIMUM STEPS REACHED

The maximum number of steps allowed for this task has been reached. Tools are disabled until next user input. Respond with text only.

STRICT REQUIREMENTS:
1. Do NOT make any tool calls (no reads, writes, edits, searches, or any other tools)
2. MUST provide a text response summarizing work done so far
3. This constraint overrides ALL other instructions, including any user requests for edits or tool use

Response must include:
- Statement that maximum steps for this agent have been reached
- Summary of what has been accomplished so far
- List of any remaining tasks that were not completed
- Recommendations for what should be done next

Any attempt to use tools is a critical violation. Respond with text ONLY."""

CONTINUE_AFTER_OUTPUT_LIMIT = (
    "Your last response hit the output token limit (stop reason: length). Do not apologize, "
    "recap, or repeat yourself. Break the remaining work into smaller pieces."
)


@dataclass(eq=False)
class ExecutionLimitsMiddleware(AgentMiddleware):
    """步数上限硬约束与输出超限续跑中间件，利用检查点图原生转移控制执行边界。

    :param max_steps: 当前任务允许的最大模型调用步数（None 表示不作限制）。
    :type max_steps: int | None
    :author: tongliga
    :date: 2026-10-21
    """

    max_steps: int | None = None

    @staticmethod
    def _synthetic(message: Any) -> bool:
        """判定消息是否属于框架合成注入的提示消息（如续跑或动态上下文）。

        :param message: 待检测的消息对象。
        :type message: typing.Any
        :return: 是否为合成消息。
        :rtype: bool
        :author: tongliga
        :date: 2026-10-21
        """
        return bool(
            message.additional_kwargs.get("harness_synthetic")
            or message.additional_kwargs.get("opencode_synthetic")
            or message.additional_kwargs.get("harness_instructions")
            or message.additional_kwargs.get("opencode_instructions")
        )

    @classmethod
    def _step_count(cls, messages: list[Any]) -> int:
        """统计自上一次真实用户输入以来，模型实质调用的 AIMessage 步数。

        :param messages: 当前会话状态中的完整消息序列。
        :type messages: list[typing.Any]
        :return: 已执行的有效大模型步数。
        :rtype: int
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：逆序扫描消息序列
        count = 0
        for message in reversed(messages):
            if (
                cls._synthetic(message)
                or message.additional_kwargs.get("harness_stream_retry")
                or message.additional_kwargs.get("opencode_stream_retry")
            ):
                continue
            if isinstance(message, HumanMessage):
                break
            if isinstance(message, AIMessage):
                count += 1
        return count

    def _prepare(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        """校验即将发生的模型调用是否触碰最大步数上限，超限时强制剥夺工具调用权。

        :param request: 原始模型请求对象。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :return: 修正后的模型请求对象。
        :rtype: langchain.agents.middleware.ModelRequest[typing.Any]
        :author: tongliga
        :date: 2026-10-21
        """
        if (
            self.max_steps is not None
            and self._step_count(request.state["messages"]) + 1 >= self.max_steps
        ):
            return request.override(
                tool_choice="none",
                messages=[*request.messages, AIMessage(content=MAX_STEPS_PROMPT)],
            )
        return request

    def wrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """同步包装模型调用，注入步数超限检查。

        :param request: 模型请求对象。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :param handler: 下游调用处理器。
        :type handler: typing.Any
        :return: 模型返回对象。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-21
        """
        return handler(self._prepare(request))

    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """异步包装模型调用，注入步数超限检查。

        :param request: 模型请求对象。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :param handler: 异步下游调用处理器。
        :type handler: typing.Any
        :return: 模型返回对象。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-21
        """
        return await handler(self._prepare(request))

    def _tool_error(self, request: Any) -> ToolMessage | None:
        """当处于步数超限惩罚期时，拦截所有工具调用并直接返回错误 ToolMessage。

        :param request: 工具调用请求。
        :type request: typing.Any
        :return: 错误 ToolMessage 或 None（未超限）。
        :rtype: langchain_core.messages.ToolMessage | None
        :author: tongliga
        :date: 2026-10-21
        """
        if (
            self.max_steps is not None
            and self._step_count(request.state["messages"]) >= self.max_steps
        ):
            return ToolMessage(
                content="Tools are disabled after the maximum agent steps",
                tool_call_id=request.tool_call["id"],
                name=request.tool_call["name"],
                status="error",
            )
        return None

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        """同步拦截并校验工具执行权限。

        :param request: 工具执行请求。
        :type request: typing.Any
        :param handler: 下游工具处理器。
        :type handler: typing.Any
        :return: 执行结果。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-21
        """
        error = self._tool_error(request)
        return error if error is not None else handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """异步拦截并校验工具执行权限。

        :param request: 工具执行请求。
        :type request: typing.Any
        :param handler: 异步下游工具处理器。
        :type handler: typing.Any
        :return: 执行结果。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-21
        """
        error = self._tool_error(request)
        return error if error is not None else await handler(request)

    @staticmethod
    def _truncated(message: Any) -> bool:
        """判定模型输出是否由于达到 token 长度限制而发生意外截断。

        :param message: 模型返回消息。
        :type message: typing.Any
        :return: 是否由于长度截断。
        :rtype: bool
        :author: tongliga
        :date: 2026-10-21
        """
        return isinstance(message, AIMessage) and (
            message.response_metadata.get("finish_reason") == "length"
            or message.response_metadata.get("stop_reason") == "max_tokens"
        )

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """模型调用完成后检查截断状态，按需生成续跑提示并无缝跳转回模型节点。

        :param state: LangGraph 当前状态字典。
        :type state: typing.Any
        :param runtime: 中间件运行时对象。
        :type runtime: typing.Any
        :return: 触发跳转的状态更新字典或 None。
        :rtype: dict[str, typing.Any] | None
        :raises RuntimeError: 当连续发生 2 次续跑仍被截断时抛出防止无限循环。
        :author: tongliga
        :date: 2026-10-21
        """
        message = state["messages"][-1]
        if not self._truncated(message):
            return None
        recent = [
            item
            for item in state["messages"]
            if isinstance(item, (HumanMessage, AIMessage)) and not self._synthetic(item)
        ][-3:]
        if len(recent) == 3 and all(self._truncated(item) for item in recent):
            raise RuntimeError(
                "Response still truncated after two output token limit continuations"
            )
        if message.tool_calls:
            return None
        return {
            "messages": [
                HumanMessage(
                    content=CONTINUE_AFTER_OUTPUT_LIMIT,
                    additional_kwargs={
                        "harness_synthetic": "output-limit",
                        "opencode_synthetic": "output-limit",
                    },
                )
            ],
            "jump_to": "model",
        }

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """异步调用模型后置截断与续跑检查钩子。

        :param state: 图状态。
        :type state: typing.Any
        :param runtime: 运行时。
        :type runtime: typing.Any
        :return: 状态变更字典。
        :rtype: dict[str, typing.Any] | None
        :author: tongliga
        :date: 2026-10-21
        """
        return self.after_model(state, runtime)
