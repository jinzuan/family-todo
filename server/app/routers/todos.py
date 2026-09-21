"""待办路由：/api/todos*（发布、列表、勾选、子待办、软删）。

写操作先提交事务，再广播 WebSocket 事件，避免客户端收到事件后读不到数据。
"""

import re
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..auth import AuthContext, get_db, require_auth
from ..schemas import (
    BulkAckRequest,
    BulkCreateRequest,
    BulkUpdateRequest,
    ClearRequest,
    CompleteRequest,
    TodoIn,
    TodoUpdate,
)
from ..services import stats_service, todo_service
from ..ws_manager import manager

router = APIRouter(tags=["todos"])

# date 参数：6 个语义 token + 具体日期；缺省=today。非法值 400，不静默当今天。
# done=已完成（只出已处理项 status=1，忽略 include_done）。
_DATE_TOKENS = {"today", "future", "overdue", "persistent", "done", "all"}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 无 mine 的 PATCH 只允许改「日期窗口」——用于执行端「重新唤起已过期待办」；
# 任何内容字段（标题/标签/重要等）都要求是本设备发布的。
_WINDOW_FIELDS = {"start_date", "end_date", "due_date", "persistent"}


def _resolve_date_mode(date: Optional[str]) -> str:
    if not date:
        return "today"
    if date in _DATE_TOKENS or _DATE_RE.match(date):
        return date
    raise HTTPException(status_code=400, detail="date 参数不合法")


async def _emit(family_id: str, event: str, data: dict) -> None:
    await manager.broadcast(family_id, {"type": event, "data": data})


async def _emit_affected(
    family_id: str,
    conn: sqlite3.Connection,
    affected_ids: list[str],
    exclude: str | None = None,
) -> None:
    """把批量变更（如连带的忽略/取消）逐条广播 todo.updated。"""
    for affected_id in affected_ids:
        if affected_id == exclude:
            continue
        node = todo_service.get_todo(conn, family_id, affected_id)
        if node:
            await _emit(family_id, "todo.updated", node)


async def _apply_mark(
    conn: sqlite3.Connection,
    family_id: str,
    todo_id: str,
    detail: str,
) -> dict:
    """已忽略 / 已取消 的公共处理：落库、提交、广播。"""
    result = todo_service.mark_status(conn, family_id, todo_id, detail)
    if result is None:
        raise HTTPException(status_code=404, detail="待办不存在")
    conn.commit()
    todo = result["todo"]
    await _emit(family_id, "todo.updated", todo)
    await _emit_affected(family_id, conn, result["affected"], exclude=todo_id)
    return todo


def _assert_editable(todo: dict, ctx: AuthContext, require_owner: bool) -> None:
    """编辑权校验：只能改未完成的；require_owner 时只能改自己发布的（参照 withdraw）。"""
    if todo["status_detail"] != todo_service.STATUS_PENDING:
        raise HTTPException(status_code=400, detail="只能修改未完成的待办")
    if (
        require_owner
        and todo["created_by"]
        and ctx.device_id
        and todo["created_by"] != ctx.device_id
    ):
        raise HTTPException(status_code=403, detail="只能修改自己发布的待办")


@router.post("/todos/bulk", status_code=status.HTTP_201_CREATED)
async def bulk_create(
    payload: BulkCreateRequest,
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    items = [item.model_dump() for item in payload.items]
    created = todo_service.bulk_create(
        conn, ctx.family_id, ctx.device_id, items, source=payload.source
    )
    stats_service.record_publish(conn, ctx.family_id, payload.source, len(created))
    conn.commit()
    for todo in created:
        await _emit(ctx.family_id, "todo.created", todo)
    return {"items": created, "count": len(created)}


@router.post("/todos/bulk-update")
async def bulk_update_todos(
    payload: BulkUpdateRequest,
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """发布端批量改：只能改本设备发布的未完成待办（统一改日期 / 标签等）。"""
    if not payload.ids:
        return {"items": [], "count": 0}
    todos = []
    for todo_id in payload.ids:
        todo = todo_service.get_todo(conn, ctx.family_id, todo_id)
        if not todo:
            raise HTTPException(status_code=404, detail="待办不存在")
        todos.append(todo)
    # 先整组校验，避免改了一半才报错
    for todo in todos:
        _assert_editable(todo, ctx, require_owner=True)
    patch = payload.patch.model_dump(exclude_unset=True)
    updated = []
    for todo in todos:
        try:
            item = todo_service.update_todo(conn, ctx.family_id, todo["id"], patch)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if item:
            updated.append(item)
    conn.commit()
    for item in updated:
        await _emit(ctx.family_id, "todo.updated", item)
    return {"items": updated, "count": len(updated)}


@router.post("/todos", status_code=status.HTTP_201_CREATED)
async def create_todo(
    payload: TodoIn,
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    todo = todo_service.create_todo(
        conn, ctx.family_id, ctx.device_id, source="manual", **payload.model_dump()
    )
    stats_service.record_publish(conn, ctx.family_id, "manual", 1)
    conn.commit()
    await _emit(ctx.family_id, "todo.created", todo)
    return todo


@router.get("/todos")
def list_todos(
    include_done: int = Query(0, description="1=含已完成"),
    flat: int = Query(0, description="1=平铺含子待办；0=仅一级"),
    mine: int = Query(0, description="1=只看本设备发布的（发布端「已发布待办」）"),
    date: Optional[str] = Query(
        None,
        description="today|future|overdue|persistent|done|all|YYYY-MM-DD；缺省=today",
    ),
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    # 未指定日期默认只看今天；overdue 强制只出未完成，done 强制只出已完成
    mode = _resolve_date_mode(date)
    created_by = ctx.device_id if mine else None
    if flat:
        items = todo_service.list_all_flat(
            conn, ctx.family_id, bool(include_done), mode, created_by
        )
    else:
        items = todo_service.list_todos(
            conn, ctx.family_id, bool(include_done), date_mode=mode, created_by=created_by
        )
    return {"items": items, "count": len(items)}


@router.get("/todo-dates")
def list_todo_dates(
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """日历红点：返回今天及未来仍有未完成待办的日期集合（YYYY-MM-DD，升序）。

    轻量接口：只回日期字符串，供前端迷你月历画红点；
    家庭维度由鉴权上下文决定，无需 family 查询参数。
    """
    dates = todo_service.list_todo_dates(conn, ctx.family_id)
    return {"dates": dates, "count": len(dates)}


@router.post("/todos/clear")
async def clear_day(
    payload: ClearRequest,
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """清空某日：服务端软删该日已了结的一级待办，保留未来/持久，并广播删除。"""
    ids = todo_service.clear_day(conn, ctx.family_id, payload.date)
    conn.commit()
    for deleted_id in ids:
        await _emit(ctx.family_id, "todo.deleted", {"id": deleted_id})
    return {"deleted": ids}


@router.patch("/todos/{todo_id}")
async def update_todo(
    todo_id: str,
    payload: TodoUpdate,
    mine: int = Query(0, description="1=只能改本设备发布的（发布端编辑自己的待办）"),
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    patch = payload.model_dump(exclude_unset=True)
    todo = todo_service.get_todo(conn, ctx.family_id, todo_id)
    if not todo:
        raise HTTPException(status_code=404, detail="待办不存在")
    # 只能改未完成；改内容或带 mine=1 时还要求本设备发布（仅日期窗口的 PATCH 例外）
    require_owner = bool(mine) or not set(patch).issubset(_WINDOW_FIELDS)
    _assert_editable(todo, ctx, require_owner=require_owner)
    try:
        updated = todo_service.update_todo(conn, ctx.family_id, todo_id, patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=404, detail="待办不存在")
    conn.commit()
    await _emit(ctx.family_id, "todo.updated", updated)
    return updated


@router.post("/todos/{todo_id}/complete")
async def complete_todo(
    todo_id: str,
    payload: Optional[CompleteRequest] = None,
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """勾选完成。

    父待办还有「待办」子项且未带 decision 时，返回
    `{"needs_decision": true, "actions": [...], "pending_count": n}`，前端据此弹三选一；
    带上 decision（complete / ignore_rest / cancel）再次调用即可。
    """
    decision = payload.decision if payload else None
    try:
        result = todo_service.complete_todo(
            conn, ctx.family_id, todo_id, decision, by_device=ctx.device_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="待办不存在")
    if result["needs_decision"]:
        return {
            "needs_decision": True,
            "actions": result["actions"],
            "pending_count": result["pending_count"],
        }
    if decision == todo_service.DECISION_CANCEL:
        # 用户取消：父不完成，数据无变化，不广播
        return result["todo"]
    conn.commit()
    todo = result["todo"]
    await _emit(
        ctx.family_id,
        "todo.completed",
        {"id": todo["id"], "status": todo["status"], "status_detail": todo["status_detail"]},
    )
    await _emit_affected(ctx.family_id, conn, result["affected"], exclude=todo_id)
    return todo


@router.post("/todos/{todo_id}/ignore")
async def ignore_todo(
    todo_id: str,
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把待办标为「已忽略」（灰色略过，算处理完）。"""
    return await _apply_mark(conn, ctx.family_id, todo_id, todo_service.STATUS_IGNORED)


@router.post("/todos/{todo_id}/cancel")
async def cancel_todo(
    todo_id: str,
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """把待办标为「已取消」（明确不做，算处理完）。"""
    return await _apply_mark(conn, ctx.family_id, todo_id, todo_service.STATUS_CANCELLED)


@router.post("/todos/{todo_id}/remove")
async def remove_todo(
    todo_id: str,
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """软删：变红 + 标「未完成」，不真删（记录仍在当天列表可追溯）。"""
    return await _apply_mark(conn, ctx.family_id, todo_id, todo_service.STATUS_UNFINISHED)


@router.post("/todos/{todo_id}/uncomplete")
async def uncomplete_todo(
    todo_id: str,
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    todo = todo_service.uncomplete_todo(conn, ctx.family_id, todo_id)
    if not todo:
        raise HTTPException(status_code=404, detail="待办不存在")
    conn.commit()
    await _emit(ctx.family_id, "todo.updated", todo)
    return todo


@router.delete("/todos/{todo_id}")
async def delete_todo(
    todo_id: str,
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    ids = todo_service.soft_delete_todo(conn, ctx.family_id, todo_id)
    if not ids:
        raise HTTPException(status_code=404, detail="待办不存在")
    conn.commit()
    for deleted_id in ids:
        await _emit(ctx.family_id, "todo.deleted", {"id": deleted_id})
    return {"deleted": ids}


@router.post("/todos/{todo_id}/ack")
async def ack_todo(
    todo_id: str,
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """执行端标记知悉单条待办（知悉人 = 当前设备）。"""
    try:
        todo = todo_service.ack_todo(conn, ctx.family_id, todo_id, ctx.device_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not todo:
        raise HTTPException(status_code=404, detail="待办不存在")
    conn.commit()
    await _emit(ctx.family_id, "todo.updated", todo)
    return todo


@router.post("/todos/ack-all")
async def ack_all_todos(
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """执行端一键知悉全部未完成待办。"""
    todos = todo_service.ack_all_todos(conn, ctx.family_id, ctx.device_id)
    conn.commit()
    for t in todos:
        if t:
            await _emit(ctx.family_id, "todo.updated", t)
    return {"items": todos, "count": len(todos)}


@router.post("/todos/ack-bulk")
async def ack_bulk_todos(
    payload: BulkAckRequest,
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """执行端多选标记知悉：只能标记未完成的，知悉人 = 当前设备。"""
    if not payload.ids:
        return {"items": [], "count": 0}
    result = todo_service.ack_bulk_todos(conn, ctx.family_id, payload.ids, ctx.device_id)
    conn.commit()
    for t in result["items"]:
        if t:
            await _emit(ctx.family_id, "todo.updated", t)
    return result


@router.post("/todos/{todo_id}/withdraw")
async def withdraw_todo(
    todo_id: str,
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    """发布者撤回：仅能撤回本设备发布的待办，软删下架（级联子孙）。"""
    todo = todo_service.get_todo(conn, ctx.family_id, todo_id)
    if not todo:
        raise HTTPException(status_code=404, detail="待办不存在")
    # 有明确发布者且非本设备 → 拒绝；无发布者（家庭令牌）不限制
    if todo["created_by"] and ctx.device_id and todo["created_by"] != ctx.device_id:
        raise HTTPException(status_code=403, detail="只能撤回自己发布的待办")
    ids = todo_service.soft_delete_todo(conn, ctx.family_id, todo_id)
    conn.commit()
    for deleted_id in ids:
        await _emit(ctx.family_id, "todo.deleted", {"id": deleted_id})
    return {"deleted": ids}


@router.post("/todos/{todo_id}/subtasks", status_code=status.HTTP_201_CREATED)
async def add_subtask(
    todo_id: str,
    payload: TodoIn,
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    todo = todo_service.add_subtask(
        conn, ctx.family_id, ctx.device_id, todo_id, **payload.model_dump()
    )
    if not todo:
        raise HTTPException(status_code=404, detail="父待办不存在")
    conn.commit()
    await _emit(ctx.family_id, "todo.created", todo)
    return todo


@router.get("/todos/{todo_id}/subtasks")
def list_subtasks(
    todo_id: str,
    ctx: AuthContext = Depends(require_auth),
    conn: sqlite3.Connection = Depends(get_db),
):
    parent = todo_service.get_todo(conn, ctx.family_id, todo_id)
    if not parent:
        raise HTTPException(status_code=404, detail="父待办不存在")
    items = todo_service.list_subtasks(conn, ctx.family_id, todo_id)
    return {"items": items, "count": len(items)}
