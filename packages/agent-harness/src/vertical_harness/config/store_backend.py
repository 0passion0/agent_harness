from enum import StrEnum


class StoreBackendType(StrEnum):
    """状态存储后端类型枚举。"""

    SQLITE = "sqlite"
    MEMORY = "memory"
