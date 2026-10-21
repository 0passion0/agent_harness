"""Offline chat model for the installed-wheel check."""

from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel


class Model(FakeMessagesListChatModel):
    """用于 Wheel 离线验证的虚假聊天模型。"""

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self
