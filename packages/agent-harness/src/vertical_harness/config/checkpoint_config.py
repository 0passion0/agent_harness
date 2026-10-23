"""LangGraph 运行检查点与会话线程配置生成模块。"""

from langchain_core.runnables import RunnableConfig


class CheckpointConfig:
    """会话检查点配置工厂，统一生成状态图调用所需的 configurable 字典。"""

    @staticmethod
    def make_checkpoint_config(thread_id: str) -> RunnableConfig:
        """创建单会话线程的原生 LangGraph 检查点配置字典。"""
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must be nonempty")
        return {"configurable": {"thread_id": thread_id}}
