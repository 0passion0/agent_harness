"""向桌面端用户发起人机提问与选择决策的交互工具模块。"""

from __future__ import annotations

from langchain_core.tools import tool
from langgraph.types import interrupt


class UserInteraction:
    """人机交互工具类，通过 LangGraph interrupt 挂起当前流程等待桌面端用户决策。"""

    @staticmethod
    @tool
    def ask_user(question: str, options: list[str] | None = None) -> str:
        """Ask for missing information or a choice. Users may pick an option or write their own answer."""
        if not question.strip() or (
            options is not None and any(not option.strip() for option in options)
        ):
            raise ValueError("Questions and answer options must not be blank")
        answer = interrupt({"kind": "question", "question": question, "options": options or []})
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("User answer must be non-empty text")
        return answer
