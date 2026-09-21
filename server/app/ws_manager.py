"""WebSocket 连接管理与广播。

按 family_id 分房间，同一家人的设备才能收到彼此的变更事件。
"""

import asyncio

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def register(self, family_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._rooms.setdefault(family_id, set()).add(ws)

    async def unregister(self, family_id: str, ws: WebSocket) -> None:
        async with self._lock:
            room = self._rooms.get(family_id)
            if room:
                room.discard(ws)
                if not room:
                    self._rooms.pop(family_id, None)

    async def broadcast(self, family_id: str, message: dict) -> None:
        room = list(self._rooms.get(family_id, set()))
        for ws in room:
            try:
                await ws.send_json(message)
            except Exception:
                await self.unregister(family_id, ws)

    def count(self, family_id: str) -> int:
        return len(self._rooms.get(family_id, set()))


manager = ConnectionManager()
