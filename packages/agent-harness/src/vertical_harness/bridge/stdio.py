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
    def __init__(
        self,
        dispatch: Callable[[str, dict[str, Any]], Awaitable[Any]],
        close: Callable[[], Awaitable[None]],
    ) -> None:
        self._dispatch = dispatch
        self._close = close
        self._output_lock = asyncio.Lock()

    @staticmethod
    def emit(frame: dict[str, Any]) -> None:
        print(json.dumps(frame, ensure_ascii=False, default=str), flush=True)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        async with self._output_lock:
            self.emit({"jsonrpc": "2.0", "method": method, "params": params})

    async def serve(self) -> None:
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
