"""切面通知（Advice）生命周期处理模块，提供前置、后置与异常通知。"""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Any

logger = logging.getLogger(__name__)


class ExecutionAspect:
    """方法与工具切面通知抽象类，提供标准切面执行生命周期钩子。

    :author: tongliga
    :date: 2026-10-21
    """

    @classmethod
    def before(cls, target_name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> float:
        """前置切面通知：在目标方法执行前被调用，记录启动时间戳与调用参数。

        :param target_name: 目标方法或工具名。
        :type target_name: str
        :param args: 位置参数元组。
        :type args: tuple[typing.Any, ...]
        :param kwargs: 关键字参数字典。
        :type kwargs: dict[str, typing.Any]
        :return: 高精度起始时间戳。
        :rtype: float
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：记录起始时间戳
        start_time = perf_counter()
        # 第二步：输出结构化前置切面日志
        logger.debug(
            "aop_before_execution",
            extra={"event_fields": {"target": target_name, "args_count": len(args)}},
        )
        return start_time

    @classmethod
    def after_returning(cls, target_name: str, result: Any, start_time: float) -> None:
        """后置返回通知：在目标方法正常执行完成后被调用，统计执行耗时。

        :param target_name: 目标方法或工具名。
        :type target_name: str
        :param result: 目标方法返回值。
        :type result: typing.Any
        :param start_time: 前置通知记录的起始时间戳。
        :type start_time: float
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：计算执行耗时（毫秒）
        duration_ms = round((perf_counter() - start_time) * 1000, 3)
        # 第二步：输出后置切面日志
        logger.debug(
            "aop_after_returning",
            extra={"event_fields": {"target": target_name, "duration_ms": duration_ms}},
        )

    @classmethod
    def after_throwing(cls, target_name: str, error: BaseException, start_time: float) -> None:
        """异常通知：在目标方法抛出异常时被调用，记录异常类型及失败耗时。

        :param target_name: 目标方法或工具名。
        :type target_name: str
        :param error: 抛出的异常对象。
        :type error: BaseException
        :param start_time: 前置通知记录的起始时间戳。
        :type start_time: float
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：计算失败耗时
        duration_ms = round((perf_counter() - start_time) * 1000, 3)
        # 第二步：记录异常告警日志
        logger.warning(
            "aop_after_throwing",
            extra={
                "event_fields": {
                    "target": target_name,
                    "error_type": type(error).__name__,
                    "duration_ms": duration_ms,
                }
            },
        )


__all__ = ["ExecutionAspect"]
