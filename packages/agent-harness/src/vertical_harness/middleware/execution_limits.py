"""OpenCode step limits and output-limit continuation on the native agent graph."""

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
class OpenCodeExecutionLimitsMiddleware(AgentMiddleware):
    """Use checkpointed messages and native graph edges, without another agent loop."""

    max_steps: int | None = None

    @staticmethod
    def _synthetic(message: Any) -> bool:
        return bool(
            message.additional_kwargs.get("opencode_synthetic")
            or message.additional_kwargs.get("opencode_instructions")
        )

    @classmethod
    def _step_count(cls, messages: list[Any]) -> int:
        count = 0
        for message in reversed(messages):
            if cls._synthetic(message) or message.additional_kwargs.get("opencode_stream_retry"):
                continue
            if isinstance(message, HumanMessage):
                break
            if isinstance(message, AIMessage):
                count += 1
        return count

    def _prepare(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
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
        return handler(self._prepare(request))

    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        return await handler(self._prepare(request))

    def _tool_error(self, request: Any) -> ToolMessage | None:
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
        error = self._tool_error(request)
        return error if error is not None else handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        error = self._tool_error(request)
        return error if error is not None else await handler(request)

    @staticmethod
    def _truncated(message: Any) -> bool:
        return isinstance(message, AIMessage) and (
            message.response_metadata.get("finish_reason") == "length"
            or message.response_metadata.get("stop_reason") == "max_tokens"
        )

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        message = state["messages"][-1]
        if not self._truncated(message):
            return None
        recent = [
            item for item in state["messages"]
            if isinstance(item, (HumanMessage, AIMessage)) and not self._synthetic(item)
        ][-3:]
        if len(recent) == 3 and all(self._truncated(item) for item in recent):
            raise RuntimeError("Response still truncated after two output token limit continuations")
        if message.tool_calls:
            return None
        return {
            "messages": [HumanMessage(
                content=CONTINUE_AFTER_OUTPUT_LIMIT,
                additional_kwargs={"opencode_synthetic": "output-limit"},
            )],
            "jump_to": "model",
        }

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self.after_model(state, runtime)
