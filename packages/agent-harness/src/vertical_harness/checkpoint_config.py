


class CheckpointConfig:
    @staticmethod
    def make_checkpoint_config(thread_id: str) -> dict[str, dict[str, str]]:
        """创建单会话线程的原生 LangGraph 检查点配置字典。

        :param thread_id: 会话线程的全局唯一标识符，必须为非空字符串。
        :type thread_id: str
        :return: 包含 configurable.thread_id 的原生 LangGraph 运行配置字典。
        :rtype: dict[str, dict[str, str]]
        :raises ValueError: 当 thread_id 不是非空字符串时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：校验 thread_id 必须为非空有效字符串
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must be nonempty")
        # 第二步：构造并返回原生 LangGraph 检查点配置字典
        return {"configurable": {"thread_id": thread_id}}
