"""埋点路由：/api/stats/*（临时功能）。"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import AuthContext, get_db, require_auth
from ..schemas import StatsEventRequest
from ..services import stats_service

router = APIRouter(tags=["stats"])


@router.post("/stats/event")
def report_event(
    payload: StatsEventRequest,
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    if payload.metric not in stats_service.METRICS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"未知指标，允许：{', '.join(stats_service.METRICS)}",
        )
    stats_service.record_event(conn, ctx.family_id, payload.metric, payload.value or 1)
    return {"ok": True}


@router.get("/stats/summary")
def summary(
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    return stats_service.get_summary(conn, ctx.family_id)
