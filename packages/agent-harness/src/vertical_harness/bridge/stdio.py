"""按行处理桌面 JSON-RPC；stdout 只承载协议帧。"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


class StdioServer:
    """基于标准输入输出的按行 JSON-RPC 2.0 服务端，提供互斥通知推送与安全退出处理。

    Attributes:
        _dispatch: 异步请求路由分发回调函数。
        _close: 服务端关闭退出时的资源清理回调函数。
        _output_lock: 标准输出并发写入排他锁，防止多任务并发 write 导致行数据交织损坏。
    """

    def __init__(
        self,
        dispatch: Callable[[str, dict[str, Any]], Awaitable[Any]],
        close: Callable[[], Awaitable[None]],
    ) -> None:
        """初始化 StdioServer，绑定请求分发与服务关闭回调。

        Args:
            dispatch: 异步请求分发可调用对象，接收 method (str) 和 params (dict[str, Any])，返回执行结果 Any。
            close: 异步资源关闭回调函数，在服务退出时执行一次性收尾。
        """
        # 异步请求路由分发回调
        self._dispatch = dispatch
        # 服务端关闭退出时的资源清理回调
        self._close = close
        # 标准输出并发写入排他锁，保证单行协议帧原子输出
        self._output_lock = asyncio.Lock()

    @staticmethod
    def emit(frame: dict[str, Any]) -> None:
        """将 JSON 字典单行序列化并直接输出至标准输出流，强制立即刷新缓冲区。

        Args:
            frame: 待发送的 JSON-RPC 2.0 协议帧字典（包含 jsonrpc, id, result/error 或 method, params）。

        Returns:
            None
        """
        print(json.dumps(frame, ensure_ascii=False, default=str), flush=True)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        """向客户端发送单向 JSON-RPC 2.0 通知消息，输出加锁保证协议帧完整无交织。

        Args:
            method: JSON-RPC 通知的方法名称。
            params: 通知携带的参数数据字典。

        Returns:
            None
        """
        async with self._output_lock:
            self.emit({"jsonrpc": "2.0", "method": method, "params": params})

    async def serve(self) -> None:
        """在标准输入流上循环按行读取请求，分发执行并在连接中断或收到 shutdown 时关闭资源。

        Returns:
            None

        Raises:
            Exception: 底层不可恢复的流读取异常传播。
        """
        # 无法解析的输入表示协议连接损坏：退出并清理，不能继续执行不明请求。
        try:
            while line := await asyncio.to_thread(sys.stdin.readline):
                request = json.loads(line)
                request_id = request.get("id")
                try:
                    result = await self._dispatch(request["method"], request.get("params", {}))
                except Exception as exc:
                    logger.error("rpc_request_failed", extra={"error_type": type(exc).__name__})
                    self.emit(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32000, "message": str(exc)},
                        }
                    )
                else:
                    self.emit({"jsonrpc": "2.0", "id": request_id, "result": result})
                if request.get("method") == "shutdown":
                    break
        finally:
            await self._close()
