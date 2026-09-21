"""认证路由：/api/auth/*、/api/me。"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from .. import auth
from ..schemas import DeviceRename, JoinRequest, JoinResponse, MeResponse

router = APIRouter(tags=["auth"])


def _me_payload(conn: sqlite3.Connection, ctx: auth.AuthContext) -> MeResponse:
    family_name = None
    device_name = None
    row = conn.execute("SELECT name FROM family WHERE id = ?", (ctx.family_id,)).fetchone()
    if row:
        family_name = row["name"]
    if ctx.device_id:
        drow = conn.execute("SELECT name FROM device WHERE id = ?", (ctx.device_id,)).fetchone()
        if drow:
            device_name = drow["name"]
    return MeResponse(
        device_id=ctx.device_id,
        family_id=ctx.family_id,
        role=ctx.role,
        kind=ctx.kind,
        family_name=family_name,
        device_name=device_name,
    )


@router.post("/auth/join", response_model=JoinResponse)
def join(
    payload: JoinRequest,
    conn: sqlite3.Connection = Depends(auth.get_db),
):
    """用家庭令牌加入，返回设备令牌（明文仅此一次）。"""
    try:
        result = auth.join(
            conn,
            payload.family_token,
            device_name=payload.device_name,
            role=payload.role,
        )
    except auth.AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    # 显式提交：设备/令牌需在响应前落库，避免紧随其后的 /api/me 读不到
    conn.commit()
    return result


@router.post("/auth/refresh")
def refresh(
    ctx: auth.AuthContext = Depends(auth.require_auth),
    conn: sqlite3.Connection = Depends(auth.get_db),
):
    """设备令牌续期/轮换：吊销旧令牌，返回新令牌。"""
    try:
        new_token = auth.rotate_device_token(conn, ctx)
    except auth.AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"device_token": new_token, "device_id": ctx.device_id}


@router.get("/me", response_model=MeResponse)
def me(
    ctx: auth.AuthContext = Depends(auth.require_auth),
    conn: sqlite3.Connection = Depends(auth.get_db),
):
    return _me_payload(conn, ctx)


@router.patch("/me", response_model=MeResponse)
def rename_device(
    payload: DeviceRename,
    ctx: auth.AuthContext = Depends(auth.require_auth),
    conn: sqlite3.Connection = Depends(auth.get_db),
):
    """修改本设备标识名；家庭令牌无设备行，拒绝。"""
    if not ctx.device_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前令牌没有设备，不能改名")
    name = (payload.device_name or "").strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="设备名不能为空")
    if len(name) > 30:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="设备名不能超过 30 字")
    conn.execute("UPDATE device SET name = ? WHERE id = ?", (name, ctx.device_id))
    conn.commit()
    return _me_payload(conn, ctx)
