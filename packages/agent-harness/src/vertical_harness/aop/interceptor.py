"""切面拦截器模块，统一协调切入点匹配与环绕通知切面调度。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from vertical_harness.aop.aspect import ExecutionAspect


class AspectInterceptor:
    """切面环绕拦截器，负责在类方法与工具调用周围织入切面生命周期。

    :author: tongliga
    :date: 2026-10-21
    """

    @classmethod
    def execute_sync(
        cls,
        target_name: str,
        target_callable: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """以切面环绕方式同步执行目标可调用对象。

        :param target_name: 目标方法或工具名称。
        :type target_name: str
        :param target_callable: 目标可调用对象。
        :type target_callable: collections.abc.Callable[..., typing.Any]
        :param args: 传递给目标方法的位置参数。
        :type args: typing.Any
        :param kwargs: 传递给目标方法的关键字参数。
        :type kwargs: typing.Any
        :return: 目标可调用对象执行产出的结果。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：触发前置通知，记录启动时间戳
        start_time = ExecutionAspect.before(target_name, args, kwargs)
        try:
            # 第二步：执行目标调用
            result = target_callable(*args, **kwargs)
            # 第三步：目标执行成功，触发后置返回通知
            ExecutionAspect.after_returning(target_name, result, start_time)
            return result
        except BaseException as error:
            # 第四步：若执行中捕获异常，触发异常通知并向外重新抛出（Fail-Fast）
            ExecutionAspect.after_throwing(target_name, error, start_time)
            raise

    @classmethod
    async def execute_async(
        cls,
        target_name: str,
        target_coroutine: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """以切面环绕方式异步执行目标异步可调用对象。

        :param target_name: 目标异步方法或工具名称。
        :type target_name: str
        :param target_coroutine: 目标异步可调用对象。
        :type target_coroutine: collections.abc.Callable[..., typing.Any]
        :param args: 传递给目标方法的位置参数。
        :type args: typing.Any
        :param kwargs: 传递给目标方法的关键字参数。
        :type kwargs: typing.Any
        :return: 目标异步方法 await 产出的结果。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：触发前置通知，记录启动时间戳
        start_time = ExecutionAspect.before(target_name, args, kwargs)
        try:
            # 第二步：异步等待目标协程执行完毕
            result = await target_coroutine(*args, **kwargs)
            # 第三步：目标执行成功，触发后置返回通知
            ExecutionAspect.after_returning(target_name, result, start_time)
            return result
        except BaseException as error:
            # 第四步：若执行中捕获异常，触发异常通知并向外重新抛出（Fail-Fast）
            ExecutionAspect.after_throwing(target_name, error, start_time)
            raise


__all__ = ["AspectInterceptor"]
