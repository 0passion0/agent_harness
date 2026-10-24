"""状态存储后端持久化类型枚举定义模块。"""

from enum import StrEnum


class StoreBackendType(StrEnum):
    """状态存储后端类型枚举。"""

    # 本地 SQLite 文件数据库持久化
    SQLITE = "sqlite"
    # 易失性纯内存存储（仅限测试或无持久化场景）
    MEMORY = "memory"
