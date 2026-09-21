"""WebSocket 路由：/ws。

协议（第 4.3 / 6.7 节）：
1. 连接后客户端第一帧发 {"type":"auth","token":"<设备令牌>"}（不放 URL，避免进访问日志）；
2. 鉴权通过后加入家庭房间，开始接收 todo.* 广播；
3. 服务端每 30s 发 ping，客户端回 pong，断线自动清理。
"""

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import auth, db
from ..ws_manager import manager

router = APIRouter()

AUTH_TIMEOUT = 10
HEARTBEAT_INTERVAL = 30


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    family_id = None
    try:
        # 首帧鉴权
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=AUTH_TIMEOUT)
        except asyncio.TimeoutError:
            await websocket.send_json({"type": "auth", "ok": False, "error": "鉴权超时"})
            await websocket.close(code=4401)
            return

        try:
            frame = json.loads(raw)
        except json.JSONDecodeError:
            frame = {}
        if frame.get("type") != "auth" or not frame.get("token"):
            await websocket.send_json({"type": "auth", "ok": False, "error": "首帧需为 auth"})
            await websocket.close(code=4401)
            return

        try:
            with db.get_conn() as conn:
                ctx = auth.authenticate(conn, frame["token"])
        except auth.AuthError as exc:
            await websocket.send_json({"type": "auth", "ok": False, "error": str(exc)})
            await websocket.close(code=4401)
            return

        family_id = ctx.family_id
        await manager.register(family_id, websocket)
        await websocket.send_json(
            {"type": "auth", "ok": True, "family_id": family_id, "role": ctx.role}
        )

        # 心跳 + 消息循环
        while True:
            try:
                msg = await asyncio.wait_for(
                    websocket.receive_text(), timeout=HEARTBEAT_INTERVAL
                )
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
                continue
            # 客户端 pong / 其他消息，M1 不处理，保活即可
            try:
                data = json.loads(msg)
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        if family_id:
            await manager.unregister(family_id, websocket)
