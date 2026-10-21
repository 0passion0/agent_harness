"""向桌面端用户发起人机提问与选择决策的交互工具模块。"""

from __future__ import annotations

from langchain_core.tools import tool
from langgraph.types import interrupt


class UserInteraction:
    """人机交互工具类，通过 LangGraph interrupt 挂起当前流程等待桌面端用户决策。

    :author: tongliga
    :date: 2026-10-21
    """

    @staticmethod
    @tool
    def ask_user(question: str, options: list[str] | None = None) -> str:
        """Ask for missing information or a choice. Users may pick an option or write their own answer.

        This asks a question, never grants system permissions.

        :param question: 向用户提出的清晰、具体的疑问或缺失信息说明。
        :type question: str
        :param options: 可选的候选项列表，供用户单选或填写自定义回复。
        :type options: list[str] | None
        :return: 用户提交的答案或选择文本。
        :rtype: str
        :raises ValueError: 当问题为空或候选项包含空字符串，或用户回复为空时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：校验提问文本及候选项非空
        if not question.strip() or (
                options is not None and any(not option.strip() for option in options)
        ):
            raise ValueError("Questions and answer options must not be blank")

        # 第二步：调用 LangGraph 原生 interrupt 触发人机交互挂起，等待桌面端恢复
        answer = interrupt({"kind": "question", "question": question, "options": options or []})

        # 第三步：校验用户恢复传入的答案非空并返回
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("User answer must be non-empty text")
        return answer
