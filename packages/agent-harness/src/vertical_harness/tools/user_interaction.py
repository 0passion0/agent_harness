"""向桌面端用户发起人机提问与选择决策的交互工具模块。

本模块提供 `UserInteraction` 工具类，包含 `ask_user` 工具：
当智能体在任务规划或执行过程中遇到关键信息缺失、架构决策分歧或多选分支时，
通过 LangGraph 的 `interrupt` 原生机制挂起状态图执行，将问题和备选项推送至桌面客户端，
等待用户输入或点选后作为工具返回值继续推进图执行。
"""

from __future__ import annotations

from langchain_core.tools import tool
from langgraph.types import interrupt


class UserInteraction:
    """人机交互工具类，通过 LangGraph interrupt 挂起当前流程等待桌面端用户决策。

    静态方法工具集合，供智能体在需要用户输入关键凭据或做出业务取舍决策时调用。
    """

    @staticmethod
    @tool
    def ask_user(question: str, options: list[str] | None = None) -> str:
        """Ask for missing information or a choice. Users may pick an option or write their own answer.

        向用户展示提问弹窗或选项卡，并阻塞挂起等待用户给出答复或选定选项。

        Args:
            question (str): 想要向用户提出的清晰明确的问题文本，不可为空白。
            options (list[str] | None): 供用户快速点选的选择项字符串列表；若为 None 则为纯文本自由问答。

        Returns:
            str: 用户在客户端选择的选项文本或手动输入的文字回答。

        Raises:
            ValueError: 当提问内容或选项列表包含空字符串/空白字符时抛出；
                或当恢复传入的用户答案不是非空字符串时抛出。
        """
        # 校验问题文本与备选选项合法性
        if not question.strip() or (
            options is not None and any(not option.strip() for option in options)
        ):
            raise ValueError("Questions and answer options must not be blank")

        # 触发 LangGraph 原生中断，将提问挂载至状态图
        answer = interrupt({"kind": "question", "question": question, "options": options or []})

        # 校验恢复返回的用户答案
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("User answer must be non-empty text")
        return answer
