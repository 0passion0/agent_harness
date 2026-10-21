"""切面编程（AOP）切入点（Pointcut）模式匹配模块。"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PointcutMatcher:
    """切入点模式匹配器，支持基于通配符或正则表达式的目标类方法与工具匹配。

    :author: tongliga
    :date: 2026-10-21
    """

    pattern: str

    def matches(self, target_name: str) -> bool:
        """判断给定的方法名或目标标识符是否满足当前切入点表达式。

        :param target_name: 待匹配的目标类方法名或工具名字符串。
        :type target_name: str
        :return: 若匹配成功返回 True，否则返回 False。
        :rtype: bool
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：精确匹配判定
        if self.pattern == "*" or self.pattern == target_name:
            return True
        # 第二步：通配符 fnmatch 模糊匹配
        return fnmatch.fnmatch(target_name, self.pattern)


__all__ = ["PointcutMatcher"]
