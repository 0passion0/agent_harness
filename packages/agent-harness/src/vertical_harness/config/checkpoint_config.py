"""LangGraph 运行检查点与会话线程配置生成模块。"""

from langchain_core.runnables import RunnableConfig


class CheckpointConfig:
    """会话检查点配置工厂，统一生成状态图调用所需的 configurable 字典。"""

    @staticmethod
    def make_checkpoint_config(thread_id: str) -> RunnableConfig:
        """创建单会话线程的原生 LangGraph 检查点配置字典。

        Args:
            thread_id: 会话线程的唯一标识符（UUID 格式字符串），不可为空。

        Returns:
            RunnableConfig: 注入 LangGraph 状态图执行的配置字典，结构为：
                {'configurable': {'thread_id': thread_id}}。

        Raises:
            ValueError: thread_id 不是字符串或仅包含空白字符时抛出。
        """
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must be nonempty")
        return {"configurable": {"thread_id": thread_id}}
