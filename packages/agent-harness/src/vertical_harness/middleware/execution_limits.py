"""智能体任务最大执行步数限制与 Token 输出超限自动续跑中间件模块。

本模块提供 `ExecutionLimitsMiddleware` 中间件，管理复杂智能体任务的运行配额与自愈机制：
1. 任务步数严格配额：跟踪统计自最近一条真实用户输入以来累计的 `AIMessage` 轮次；
2. 步数告警与工具剥夺：当接近步数上限时（`step + 1 == max_steps`），剥夺工具调用权限（`tool_choice="none"`）并强行注入收尾提示；
3. 超限截断跳转：步数耗尽后若仍尝试推理，直接注入告警消息并利用 LangGraph 原生 `jump_to="end"` 结束图执行；
4. 输出长度超限续跑（Token limit continuation）：检测模型返回的 `finish_reason="length"`，自动构造合成提示消息 `jump_to="model"` 进行续写，最多允许 2 次连续续写。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

# 步数上限到达时强制注入的系统提示词
MAX_STEPS_PROMPT: str = """CRITICAL - MAXIMUM STEPS REACHED

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

# 模型生成遭遇最大 token 上限截断时自动注入的续写驱动提示词
CONTINUE_AFTER_OUTPUT_LIMIT: str = (
    "Your last response hit the output token limit (stop reason: length). Do not apologize, "
    "recap, or repeat yourself. Break the remaining work into smaller pieces."
)


@dataclass(eq=False)
class ExecutionLimitsMiddleware(AgentMiddleware):
    """步数上限硬约束与输出超限续跑中间件，利用检查点图原生转移控制执行边界。

    Attributes:
        max_steps (int | None): 单次任务允许执行的最大 AIMessage 模型交互轮数；若为 None 则不设步数上限。
    """

    max_steps: int | None = None

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """超限后最多再给一次文字收尾机会，随后由图强制结束。

        Args:
            state (Any): 当前状态图状态字典，包含会话消息列表 `state["messages"]`。
            runtime (Any): 工具运行时实体。

        Returns:
            dict[str, Any] | None: 若已超过最大步数限制，返回包含结束提示并指示 `jump_to="end"` 的字典；
                若未超限则返回 None 放行。
        """
        if self.max_steps is None or self._step_count(state["messages"]) <= self.max_steps:
            return None
        return {
            "messages": [
                AIMessage(
                    content=(
                        "Maximum agent steps reached. The run was stopped after the final response "
                        "attempt. Remaining work is incomplete; send a new message to continue."
                    )
                )
            ],
            "jump_to": "end",
        }

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """异步执行相同的终止检查。

        Args:
            state (Any): 状态字典。
            runtime (Any): 运行时实体。

        Returns:
            dict[str, Any] | None: 终止指令字典或 None。
        """
        return self.before_model(state, runtime)

    @staticmethod
    def _synthetic(message: Any) -> bool:
        """判定消息是否属于框架合成注入的提示消息（如续跑或动态上下文）。

        Args:
            message (Any): 待判定的 LangChain 消息对象。

        Returns:
            bool: 若该消息属于内部合成消息则返回 True。
        """
        return bool(
            message.additional_kwargs.get("harness_synthetic")
            or message.additional_kwargs.get("harness_instructions")
        )

    @classmethod
    def _step_count(cls, messages: list[Any]) -> int:
        """统计自上一次真实用户输入以来，模型实质调用的 AIMessage 步数。

        Args:
            messages (list[Any]): 历史消息列表。

        Returns:
            int: 统计出的实质有效智能体执行步数。
        """
        count = 0
        for message in reversed(messages):
            if cls._synthetic(message) or message.additional_kwargs.get("harness_stream_retry"):
                continue
            if isinstance(message, HumanMessage):
                break
            if isinstance(message, AIMessage):
                count += 1
        return count

    def _prepare(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        """校验即将发生的模型调用是否触碰最大步数上限，超限时强制剥夺工具调用权。

        Args:
            request (ModelRequest[Any]): 模型调用请求对象。

        Returns:
            ModelRequest[Any]: 禁用工具并追加收尾提示词后的模型请求对象（若即将达到步数上限），
                否则返回原始请求。
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

        Args:
            request (ModelRequest[Any]): 模型调用请求。
            handler (Any): 下一级模型执行器。

        Returns:
            Any: 模型调用结果。
        """
        return handler(self._prepare(request))

    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """异步包装模型调用，注入步数超限检查。

        Args:
            request (ModelRequest[Any]): 模型调用请求。
            handler (Any): 下一级异步执行器。

        Returns:
            Any: 异步模型执行响应。
        """
        return await handler(self._prepare(request))

    def _tool_error(self, request: Any) -> ToolMessage | None:
        """当处于步数超限惩罚期时，拦截所有工具调用并直接返回错误 ToolMessage。

        Args:
            request (Any): 工具调用请求。

        Returns:
            ToolMessage | None: 步数超限时返回说明错误的 ToolMessage；否则返回 None。
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

        Args:
            request (Any): 工具请求。
            handler (Any): 下一级工具调用处理器。

        Returns:
            Any: 工具调用结果或超限错误消息。
        """
        error = self._tool_error(request)
        return error if error is not None else handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """异步拦截并校验工具执行权限。

        Args:
            request (Any): 工具请求。
            handler (Any): 下一级异步工具处理器。

        Returns:
            Any: 异步工具调用结果或超限错误消息。
        """
        error = self._tool_error(request)
        return error if error is not None else await handler(request)

    @staticmethod
    def _truncated(message: Any) -> bool:
        """判定模型输出是否由于达到 token 长度限制而发生意外截断。

        Args:
            message (Any): 待判定的响应消息。

        Returns:
            bool: 若响应元数据中 `finish_reason` 为 `"length"` 或 `stop_reason` 为 `"max_tokens"` 则返回 True。
        """
        return isinstance(message, AIMessage) and (
            message.response_metadata.get("finish_reason") == "length"
            or message.response_metadata.get("stop_reason") == "max_tokens"
        )

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """模型调用完成后检查截断状态，按需生成续跑提示并无缝跳转回模型节点。

        若模型输出因长度被截断，且未包含工具调用，则注入合成续写提示并跳回 `"model"`；
        若连续截断超过 2 次，则抛出 RuntimeError 防范死循环。

        Args:
            state (Any): 状态字典。
            runtime (Any): 运行时上下文。

        Returns:
            dict[str, Any] | None: 包含续写提示并指示 `jump_to="model"` 的状态更新字典；
                若未截断或包含工具调用则返回 None。

        Raises:
            RuntimeError: 当连续两次尝试续写后仍然遭遇输出长度截断时抛出。
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
                    },
                )
            ],
            "jump_to": "model",
        }

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """异步调用模型后置截断与续跑检查钩子。

        Args:
            state (Any): 状态字典。
            runtime (Any): 运行时上下文。

        Returns:
            dict[str, Any] | None: 续写状态更新字典或 None。
        """
        return self.after_model(state, runtime)
