"""待办业务：增删改查、子待办、父子完成规则。

父子完成规则（用户 09-12 定稿，第 3.3 节）：
- 待办状态细分：pending（待办）/ completed（已完成）/ ignored（已忽略）/ cancelled（已取消）；
- 勾父级时若还有「待办」子项 → 返回三选一提示（complete / ignore_rest / cancel）；
- 子项全部非「待办」→ 父待办自动完成，并向上递归；
- 已忽略 / 已取消都算处理完，因此也会触发父级自动完成；
- 取消某个子项处理完成 / 新增未处理子项 → 所有祖先回到待办。
"""

import sqlite3
import uuid
from datetime import date, timedelta

from . import stats_service
from ..utils.time import today_local_iso, utcnow_iso

VALID_CATEGORIES = {"shopping", "errand", "other"}

# status_detail 细分状态；status(0/1) 保持旧语义：0=待办，1=已处理
STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
STATUS_IGNORED = "ignored"
STATUS_CANCELLED = "cancelled"
STATUS_UNFINISHED = "unfinished"  # 软删：变红 + 标「未完成」，不真删

# status_detail → 旧 status 字段的映射（已忽略/已取消/未完成都算处理完）
_DETAIL_TO_STATUS = {
    STATUS_PENDING: 0,
    STATUS_COMPLETED: 1,
    STATUS_IGNORED: 1,
    STATUS_CANCELLED: 1,
    STATUS_UNFINISHED: 1,
}

# 勾父级存在待办子项时，返回给前端的三选一动作
DECISION_COMPLETE = "complete"
DECISION_IGNORE_REST = "ignore_rest"
DECISION_CANCEL = "cancel"
COMPLETE_ACTIONS = [DECISION_COMPLETE, DECISION_IGNORE_REST, DECISION_CANCEL]
VALID_DECISIONS = set(COMPLETE_ACTIONS)


def _now() -> str:
    return utcnow_iso()


def _row_to_dict(row: sqlite3.Row) -> dict:
    data = {
        "id": row["id"],
        "family_id": row["family_id"],
        "parent_id": row["parent_id"],
        "title": row["title"],
        "qty": row["qty"],
        "unit": row["unit"],
        "price": row["price"],
        "category": row["category"],
        "tag": row["tag"],
        "important": bool(row["important"]),
        "note": row["note"],
        # 兼容旧读取方：有窗口时回吐 end_date 作为 due_date，持久为 NULL
        "due_date": row["end_date"] if row["end_date"] is not None else row["due_date"],
        "start_date": row["start_date"],
        "end_date": row["end_date"],
        "status": row["status"],
        "status_detail": row["status_detail"],
        "sort_order": row["sort_order"],
        "source": row["source"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
        "completed_by": row["completed_by"],
        "acked_by": row["acked_by"],
        "ack_at": row["ack_at"],
        "deleted_at": row["deleted_at"],
    }
    # 只有带 JOIN device 的查询会带 creator_name；其余查询安全回退为 None
    keys = row.keys() if hasattr(row, "keys") else []
    data["creator_name"] = row["creator_name"] if "creator_name" in keys else None
    # 反馈闭环：知悉人 / 完成人 的设备名（有 JOIN 才有，否则 None）
    data["acker_name"] = row["acker_name"] if "acker_name" in keys else None
    data["completer_name"] = row["completer_name"] if "completer_name" in keys else None
    return data


def _next_sort_order(conn: sqlite3.Connection, family_id: str, parent_id: str | None) -> int:
    if parent_id:
        row = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS m FROM todo "
            "WHERE family_id = ? AND parent_id = ? AND deleted_at IS NULL",
            (family_id, parent_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS m FROM todo "
            "WHERE family_id = ? AND parent_id IS NULL AND deleted_at IS NULL",
            (family_id,),
        ).fetchone()
    return int(row["m"]) + 1


def get_todo(conn: sqlite3.Connection, family_id: str, todo_id: str) -> dict | None:
    row = conn.execute(
        "SELECT t.*, d.name AS creator_name, a.name AS acker_name, "
        "c.name AS completer_name FROM todo t "
        "LEFT JOIN device d ON d.id = t.created_by "
        "LEFT JOIN device a ON a.id = t.acked_by "
        "LEFT JOIN device c ON c.id = t.completed_by "
        "WHERE t.id = ? AND t.family_id = ? AND t.deleted_at IS NULL",
        (todo_id, family_id),
    ).fetchone()
    return _row_to_dict(row) if row else None


def normalize_window(
    start_date: str | None,
    end_date: str | None,
    persistent: bool,
    due_date: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """把入参规范化成 DB 里唯一两种形态（返回 start, end, due 回显值）。

    - persistent=True → 双空=持久，due_date 也置空；
    - 只给 due_date → 映射单日窗口 start=end=due_date（兼容旧调用方）；
    - 只给 start/end 一边 → 补齐另一边；
    - 全空且非持久 → 默认今天（兼容 Android/旧前端“缺省=今天”）；
    - start > end → 抛 ValueError（路由转 400）。
    """
    if persistent:
        return None, None, None
    start = (start_date or "").strip() or None
    end = (end_date or "").strip() or None
    due = (due_date or "").strip() or None
    if start is None and end is None and due is not None:
        start = end = due
    elif start is not None or end is not None:
        start = start or end
        end = end or start
    if start is None and end is None:
        start = end = today_local_iso()
    if start and end and start > end:
        raise ValueError("start_date 不能晚于 end_date")
    return start, end, end


def create_todo(
    conn: sqlite3.Connection,
    family_id: str,
    device_id: str | None,
    *,
    title: str,
    qty: float | None = None,
    unit: str | None = None,
    price: float | None = None,
    category: str = "other",
    tag: str | None = None,
    note: str | None = None,
    due_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    persistent: bool = False,
    important: bool = False,
    parent_id: str | None = None,
    source: str = "manual",
    sort_order: int | None = None,
) -> dict:
    if category not in VALID_CATEGORIES:
        category = "other"
    start_date, end_date, due_date = normalize_window(
        start_date, end_date, persistent, due_date
    )
    todo_id = str(uuid.uuid4())
    now = _now()
    if sort_order is None:
        sort_order = _next_sort_order(conn, family_id, parent_id)
    conn.execute(
        """INSERT INTO todo(id, family_id, parent_id, title, qty, unit, price,
                            category, tag, important, note, due_date, start_date, end_date,
                            status, status_detail, sort_order,
                            source, created_by, created_at, updated_at)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)""",
        (
            todo_id, family_id, parent_id, title, qty, unit, price,
            category, tag, 1 if important else 0, note, due_date, start_date, end_date,
            STATUS_PENDING, sort_order, source,
            device_id, now, now,
        ),
    )
    if parent_id:
        _reset_ancestors_incomplete(conn, family_id, parent_id)
    return get_todo(conn, family_id, todo_id)


def bulk_create(
    conn: sqlite3.Connection,
    family_id: str,
    device_id: str | None,
    items: list[dict],
    source: str = "oneline",
) -> list[dict]:
    created = []
    for item in items:
        created.append(
            create_todo(
                conn,
                family_id,
                device_id,
                title=item["title"],
                qty=item.get("qty"),
                unit=item.get("unit"),
                price=item.get("price"),
                category=item.get("category", "other"),
                tag=item.get("tag"),
                note=item.get("note"),
                due_date=item.get("due_date"),
                start_date=item.get("start_date"),
                end_date=item.get("end_date"),
                persistent=bool(item.get("persistent")),
                important=bool(item.get("important")),
                source=source,
            )
        )
    return created


def _window_clause(mode: str | None) -> tuple[str, list, bool, bool]:
    """日期窗口过滤片段，返回 (SQL 片段, 参数, 是否强制未完, 是否强制已完成)。

    语义（评审锁定，兼容旧 due_date）：
    - None/"all"   → 不限日期；
    - "today"      → 覆盖今天的（含区间）+ 持久待办；
    - "future"     → 尚未开始（起点 > 今天）；
    - "overdue"    → 已过结束（终点 < 今天），强制只出未完成；
    - "done"       → 只出已处理项（status=1：completed 及其它已了结），忽略日期；
    - "persistent" → 仅双空持久；
    - "YYYY-MM-DD" → 覆盖该日的（不含持久）。

    旧数据用 COALESCE(start_date, due_date) 兜底，回填未跑也不丢。
    """
    if not mode or mode == "all":
        return "", [], False, False
    if mode == "done":
        # 已完成筛选：与日期无关，强制只出已处理项（status_detail=completed 或 status=1）
        return "", [], False, True
    today = today_local_iso()
    if mode == "today":
        return (
            " AND ((COALESCE(start_date, due_date) <= ? "
            "AND COALESCE(end_date, due_date) >= ?) "
            "OR (start_date IS NULL AND end_date IS NULL AND due_date IS NULL))",
            [today, today],
            False,
            False,
        )
    if mode == "future":
        return (" AND (COALESCE(start_date, due_date) > ?)", [today], False, False)
    if mode == "overdue":
        return (" AND (COALESCE(end_date, due_date) < ?)", [today], True, False)
    if mode == "persistent":
        return (
            " AND (start_date IS NULL AND end_date IS NULL AND due_date IS NULL)",
            [],
            False,
            False,
        )
    # 具体某天
    return (
        " AND (COALESCE(start_date, due_date) <= ? "
        "AND COALESCE(end_date, due_date) >= ?)",
        [mode, mode],
        False,
        False,
    )


def list_todos(
    conn: sqlite3.Connection,
    family_id: str,
    include_done: bool = False,
    parent_id: str | None = None,
    date_mode: str | None = None,
    created_by: str | None = None,
) -> list[dict]:
    clause, date_params, force_pending, force_done = _window_clause(date_mode)
    # done=已完成筛选属「历史查询」，需含被清空/撤回而软删的已处理项；
    # 其余模式（today/future/overdue 等）照旧排除软删。
    deleted_filter = (
        "(t.deleted_at IS NULL OR t.status = 1)" if force_done else "t.deleted_at IS NULL"
    )
    sql = [
        "SELECT t.*, d.name AS creator_name, a.name AS acker_name, "
        "c.name AS completer_name FROM todo t "
        "LEFT JOIN device d ON d.id = t.created_by "
        "LEFT JOIN device a ON a.id = t.acked_by "
        "LEFT JOIN device c ON c.id = t.completed_by "
        f"WHERE t.family_id = ? AND {deleted_filter}",
    ]
    params: list = [family_id]
    if created_by:
        sql.append("AND t.created_by = ?")
        params.append(created_by)
    if clause:
        sql.append(clause.strip())
        params.extend(date_params)
    if force_done:
        sql.append("AND t.status = 1")
    elif not include_done or force_pending:
        sql.append("AND t.status = 0")
    if parent_id is None:
        # 默认只列一级；子待办由 /subtasks 或 include 参数获取
        sql.append("AND t.parent_id IS NULL")
    else:
        sql.append("AND t.parent_id = ?")
        params.append(parent_id)
    # 排序：未完成在前；未完成里重要置顶；再按写入顺序
    sql.append(
        "ORDER BY (CASE WHEN t.status = 0 THEN 0 ELSE 1 END) ASC, "
        "t.important DESC, t.sort_order ASC, t.created_at ASC"
    )
    rows = conn.execute(" ".join(sql), params).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_all_flat(
    conn: sqlite3.Connection,
    family_id: str,
    include_done: bool = False,
    date_mode: str | None = None,
    created_by: str | None = None,
) -> list[dict]:
    """平铺列出所有层级（含子待办），前端可按 parent_id 组树。"""
    clause, date_params, force_pending, force_done = _window_clause(date_mode)
    # 与 list_todos 同规则：done 含软删的已处理项，其它模式排除软删。
    deleted_filter = (
        "(t.deleted_at IS NULL OR t.status = 1)" if force_done else "t.deleted_at IS NULL"
    )
    sql = (
        "SELECT t.*, d.name AS creator_name, a.name AS acker_name, "
        "c.name AS completer_name FROM todo t "
        "LEFT JOIN device d ON d.id = t.created_by "
        "LEFT JOIN device a ON a.id = t.acked_by "
        "LEFT JOIN device c ON c.id = t.completed_by "
        f"WHERE t.family_id = ? AND {deleted_filter}"
    )
    params: list = [family_id]
    if created_by:
        sql += " AND t.created_by = ?"
        params.append(created_by)
    if clause:
        sql += clause
        params.extend(date_params)
    if force_done:
        sql += " AND t.status = 1"
    elif not include_done or force_pending:
        sql += " AND t.status = 0"
    sql += (
        " ORDER BY (CASE WHEN t.status = 0 THEN 0 ELSE 1 END) ASC, "
        "t.important DESC, t.sort_order ASC, t.created_at ASC"
    )
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_todo_dates(
    conn: sqlite3.Connection,
    family_id: str,
    since: str | None = None,
) -> list[str]:
    """日历红点数据：返回「今天及未来有未完成待办」的日期集合（升序）。

    - 只统计未软删、status=0（未处理）且带日期窗口的待办；持久待办无日期不参与；
    - 区间待办把 [start, end] 按天展开，只保留 since（默认今天）当天及之后的日子；
    - 旧字段 due_date 用 COALESCE 兜底，与列表查询语义一致；
    - 脏日期跳过，绝不因单条坏数据影响整个日历。
    """
    start_day = since or today_local_iso()
    rows = conn.execute(
        "SELECT COALESCE(start_date, due_date) AS start_day, "
        "COALESCE(end_date, due_date) AS end_day "
        "FROM todo "
        "WHERE family_id = ? AND deleted_at IS NULL AND status = 0 "
        "AND COALESCE(end_date, due_date) >= ? "
        "AND COALESCE(start_date, due_date) IS NOT NULL",
        (family_id, start_day),
    ).fetchall()
    try:
        since_date = date.fromisoformat(start_day)
    except ValueError:
        since_date = date.today()
    days: set[str] = set()
    for row in rows:
        try:
            window_start = date.fromisoformat(row["start_day"])
            window_end = date.fromisoformat(row["end_day"])
        except (TypeError, ValueError):
            continue
        cursor = max(window_start, since_date)
        while cursor <= window_end:
            days.add(cursor.isoformat())
            cursor += timedelta(days=1)
    return sorted(days)


def update_todo(conn: sqlite3.Connection, family_id: str, todo_id: str, patch: dict) -> dict | None:
    """局部更新。

    - 普通字段沿用「非空才更新」；
    - note/日期字段支持显式 null：真写 NULL（日期双空即转持久）；
    - persistent=true 直接转持久；
    - 日期只给一边自动补齐，start>end 抛 ValueError（路由转 400）。
    """
    todo = get_todo(conn, family_id, todo_id)
    if not todo:
        return None
    patch = dict(patch)
    persistent = bool(patch.pop("persistent", False))
    if persistent:
        patch["start_date"] = None
        patch["end_date"] = None
        patch["due_date"] = None
    elif "start_date" in patch or "end_date" in patch:
        start = patch.get("start_date", todo.get("start_date"))
        end = patch.get("end_date", todo.get("end_date"))
        if start is None and end is None:
            # 双空 → 转持久，due_date 一并清掉
            patch["start_date"] = None
            patch["end_date"] = None
            patch["due_date"] = None
        else:
            start = start or end
            end = end or start
            if start > end:
                raise ValueError("start_date 不能晚于 end_date")
            patch["start_date"] = start
            patch["end_date"] = end
            # 旧字段同步，保证旧读取方不丢日期
            patch["due_date"] = end
    elif patch.get("due_date"):
        # 旧调用方只改 due_date → 映射单日窗口
        patch["start_date"] = patch["due_date"]
        patch["end_date"] = patch["due_date"]
    fields = []
    params: list = []
    nullable = {"note", "tag", "due_date", "start_date", "end_date"}
    for key in (
        "title", "qty", "unit", "price", "category", "tag", "important",
        "note", "due_date", "start_date", "end_date", "sort_order",
    ):
        if key not in patch:
            continue
        value = patch[key]
        if value is None and key not in nullable:
            continue
        if key == "category" and value is not None and value not in VALID_CATEGORIES:
            value = "other"
        if key == "important":
            value = 1 if value else 0
        fields.append(f"{key} = ?")
        params.append(value)
    if not fields:
        return todo
    fields.append("updated_at = ?")
    params.append(_now())
    params.extend([todo_id, family_id])
    conn.execute(
        f"UPDATE todo SET {', '.join(fields)} WHERE id = ? AND family_id = ?",
        params,
    )
    return get_todo(conn, family_id, todo_id)


def complete_todo(
    conn: sqlite3.Connection,
    family_id: str,
    todo_id: str,
    decision: str | None = None,
    by_device: str | None = None,
) -> dict | None:
    """勾选完成，按第 3.3 节父子交互规则处理。

    - 有「待办」子项且未给 decision → 返回三选一提示，不改动数据；
    - decision=complete → 父与全部待办子项一并标为已完成（递归）；
    - decision=ignore_rest → 父完成，未勾的待办子项递归标为「已忽略」；
    - decision=cancel → 父不完成，数据不变。
    - by_device：完成人（勾选完成那一方），记录到 completed_by。

    返回 None（不存在）或结果字典：
    {"needs_decision": True, "actions": [...], "pending_count": n}
    {"needs_decision": False, "todo": {...}, "affected": [被改动的 id...]}
    """
    todo = get_todo(conn, family_id, todo_id)
    if not todo:
        return None
    if decision is not None and decision not in VALID_DECISIONS:
        raise ValueError(f"未知的完成选项: {decision}")

    pending_children = _pending_children(conn, family_id, todo_id)
    if pending_children and decision is None:
        return {
            "needs_decision": True,
            "actions": list(COMPLETE_ACTIONS),
            "pending_count": len(pending_children),
        }

    if decision == DECISION_CANCEL:
        # 用户取消：父不完成
        return {"needs_decision": False, "todo": todo, "affected": []}

    now = _now()
    _do_complete(conn, todo_id, by_device, now)
    affected: list[str] = []
    if decision == DECISION_IGNORE_REST:
        affected = _mark_detail_recursive(
            conn, family_id, [c["id"] for c in pending_children], STATUS_IGNORED
        )
    elif decision == DECISION_COMPLETE:
        for c in pending_children:
            _do_complete(conn, c["id"], by_device, now)
            affected.append(c["id"])
        _mark_detail_recursive(
            conn, family_id, [c["id"] for c in pending_children], STATUS_COMPLETED
        )
    affected.extend(_complete_parents_if_done(conn, family_id, todo["parent_id"], by_device=by_device))
    return {
        "needs_decision": False,
        "todo": get_todo(conn, family_id, todo_id),
        "affected": affected,
    }


def _do_complete(conn: sqlite3.Connection, todo_id: str, by_device: str | None, now: str) -> None:
    """单条标记为已完成，并记录完成人与完成时间。"""
    conn.execute(
        "UPDATE todo SET status = 1, status_detail = ?, completed_at = ?, "
        "completed_by = ?, updated_at = ? WHERE id = ?",
        (STATUS_COMPLETED, now, by_device, now, todo_id),
    )


def mark_status(
    conn: sqlite3.Connection,
    family_id: str,
    todo_id: str,
    detail: str,
) -> dict | None:
    """把某待办（及其待办后代）标为「已忽略 / 已取消 / 未完成」，返回结果字典。"""
    if detail not in (STATUS_IGNORED, STATUS_CANCELLED, STATUS_UNFINISHED):
        raise ValueError(f"不支持的状态: {detail}")
    todo = get_todo(conn, family_id, todo_id)
    if not todo:
        return None
    affected = _mark_detail_recursive(conn, family_id, [todo_id], detail)
    # 该节点被当作处理完 → 可能触发父级自动完成
    affected.extend(_complete_parents_if_done(conn, family_id, todo["parent_id"]))
    return {"todo": get_todo(conn, family_id, todo_id), "affected": affected}


def uncomplete_todo(conn: sqlite3.Connection, family_id: str, todo_id: str) -> dict | None:
    todo = get_todo(conn, family_id, todo_id)
    if not todo:
        return None
    now = _now()
    conn.execute(
        "UPDATE todo SET status = 0, status_detail = ?, completed_at = NULL, "
        "completed_by = NULL, updated_at = ? "
        "WHERE id = ? AND family_id = ?",
        (STATUS_PENDING, now, todo_id, family_id),
    )
    # 子待办回到待办 → 所有祖先回到待办
    _reset_ancestors_incomplete(conn, family_id, todo["parent_id"])
    return get_todo(conn, family_id, todo_id)


def ack_todo(conn: sqlite3.Connection, family_id: str, todo_id: str, by_device: str | None) -> dict | None:
    """执行端标记知悉单条待办：记录知悉人(acked_by)与知悉时间(ack_at)。"""
    todo = get_todo(conn, family_id, todo_id)
    if not todo:
        return None
    if todo["status_detail"] != STATUS_PENDING:
        raise ValueError("只能标记「未完成」待办为知悉")
    now = _now()
    conn.execute(
        "UPDATE todo SET acked_by = ?, ack_at = ?, updated_at = ? WHERE id = ? AND family_id = ?",
        (by_device, now, now, todo_id, family_id),
    )
    return get_todo(conn, family_id, todo_id)


def ack_all_todos(conn: sqlite3.Connection, family_id: str, by_device: str | None) -> list[dict]:
    """执行端一键知悉全部未完成待办（含子待办）。返回被标记的待办。"""
    now = _now()
    rows = conn.execute(
        "SELECT id FROM todo WHERE family_id = ? AND status_detail = ? AND deleted_at IS NULL",
        (family_id, STATUS_PENDING),
    ).fetchall()
    ids = [r["id"] for r in rows]
    if ids:
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE todo SET acked_by = ?, ack_at = ?, updated_at = ? "
            f"WHERE family_id = ? AND id IN ({placeholders})",
            (by_device, now, now, family_id, *ids),
        )
    return [get_todo(conn, family_id, tid) for tid in ids]


def ack_bulk_todos(conn: sqlite3.Connection, family_id: str, ids: list[str], by_device: str | None) -> dict | None:
    """执行端多选标记知悉：只能标记未完成的；返回 (成功数, 全部更新后的 todo 列表)。"""
    now = _now()
    achieved: list[dict] = []
    for tid in ids:
        todo = get_todo(conn, family_id, tid)
        if not todo or todo["status_detail"] != STATUS_PENDING:
            continue
        conn.execute(
            "UPDATE todo SET acked_by = ?, ack_at = ?, updated_at = ? WHERE id = ? AND family_id = ?",
            (by_device, now, now, tid, family_id),
        )
        achieved.append(get_todo(conn, family_id, tid))
    return {"count": len(achieved), "items": achieved}


def soft_delete_todo(conn: sqlite3.Connection, family_id: str, todo_id: str) -> list[str]:
    """软删除某待办及其所有子孙，返回被删除的 id 列表。"""
    todo = get_todo(conn, family_id, todo_id)
    if not todo:
        return []
    now = _now()
    ids = [todo_id]
    # 广度优先收集子孙
    frontier = [todo_id]
    while frontier:
        placeholders = ",".join("?" for _ in frontier)
        rows = conn.execute(
            f"SELECT id FROM todo WHERE family_id = ? AND parent_id IN ({placeholders}) "
            "AND deleted_at IS NULL",
            [family_id, *frontier],
        ).fetchall()
        frontier = [r["id"] for r in rows]
        ids.extend(frontier)
    placeholders = ",".join("?" for _ in ids)
    conn.execute(
        f"UPDATE todo SET deleted_at = ?, updated_at = ? WHERE id IN ({placeholders})",
        [now, now, *ids],
    )
    return ids


def clear_day(conn: sqlite3.Connection, family_id: str, day: str) -> list[str]:
    """清空某日：软删「覆盖该日且已了结」的一级待办，保留未来/持久。

    已了结 = status_detail != pending（completed/ignored/cancelled/unfinished）。
    """
    clause, params, _, _ = _window_clause(day)
    rows = conn.execute(
        "SELECT id FROM todo WHERE family_id = ? AND deleted_at IS NULL "
        "AND parent_id IS NULL AND status_detail != ?" + clause,
        [family_id, STATUS_PENDING, *params],
    ).fetchall()
    ids = [r["id"] for r in rows]
    if not ids:
        return []
    now = _now()
    placeholders = ",".join("?" for _ in ids)
    conn.execute(
        f"UPDATE todo SET deleted_at = ?, updated_at = ? WHERE id IN ({placeholders})",
        [now, now, *ids],
    )
    return ids


def add_subtask(
    conn: sqlite3.Connection,
    family_id: str,
    device_id: str | None,
    parent_id: str,
    **item,
) -> dict | None:
    parent = get_todo(conn, family_id, parent_id)
    if not parent:
        return None
    item.pop("parent_id", None)
    # 子待办默认继承父项的有效期窗口 / 持久属性，避免跨天错位
    if (
        item.get("start_date") is None
        and item.get("end_date") is None
        and not item.get("persistent")
    ):
        if parent.get("start_date") is None and parent.get("end_date") is None:
            item["persistent"] = True
        else:
            item["start_date"] = parent.get("start_date")
            item["end_date"] = parent.get("end_date")
    item.pop("due_date", None)
    created = create_todo(
        conn, family_id, device_id, parent_id=parent_id,
        source=item.pop("source", "manual"), **item,
    )
    stats_service.record_event(conn, family_id, "subtask_expand_count", 1)
    return created


def list_subtasks(conn: sqlite3.Connection, family_id: str, parent_id: str) -> list[dict]:
    return list_todos(conn, family_id, include_done=True, parent_id=parent_id)


# ---- 父子完成规则内部实现 ----

def _pending_children(conn: sqlite3.Connection, family_id: str, todo_id: str) -> list[dict]:
    """列出某待办下仍为「待办」的直接子项。"""
    rows = conn.execute(
        "SELECT * FROM todo WHERE parent_id = ? AND family_id = ? "
        "AND deleted_at IS NULL AND status = 0 "
        "ORDER BY sort_order ASC, created_at ASC",
        (todo_id, family_id),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _set_detail(conn: sqlite3.Connection, todo_id: str, detail: str, now: str) -> None:
    """按细分状态同步更新 status(0/1) 与 completed_at。"""
    status = _DETAIL_TO_STATUS[detail]
    completed_at = now if status == 1 else None
    conn.execute(
        "UPDATE todo SET status = ?, status_detail = ?, completed_at = ?, updated_at = ? "
        "WHERE id = ?",
        (status, detail, completed_at, now, todo_id),
    )


def _mark_detail_recursive(
    conn: sqlite3.Connection,
    family_id: str,
    node_ids: list[str],
    detail: str,
) -> list[str]:
    """把给定节点及其所有「待办」后代设为指定状态，返回被改动的 id。

    用于「已忽略 / 已取消」：根节点按用户明确操作强制改状态，
    其后代只把仍是「待办」的收进来，已完成的不覆盖。
    """
    now = _now()
    affected: list[str] = []
    frontier = list(node_ids)
    while frontier:
        for node_id in frontier:
            _set_detail(conn, node_id, detail, now)
            affected.append(node_id)
        placeholders = ",".join("?" for _ in frontier)
        rows = conn.execute(
            f"SELECT id FROM todo WHERE family_id = ? AND parent_id IN ({placeholders}) "
            "AND deleted_at IS NULL AND status = 0",
            [family_id, *frontier],
        ).fetchall()
        frontier = [r["id"] for r in rows]
    return affected


def _complete_parents_if_done(
    conn: sqlite3.Connection, family_id: str, parent_id: str | None, by_device: str | None = None
) -> list[str]:
    """若父待办的子项全部非「待办」（completed/ignored/cancelled），父完成并向上递归。

    返回被自动完成的祖先 id 列表，供路由广播事件。
    """
    completed: list[str] = []
    current = parent_id
    while current:
        parent = conn.execute(
            "SELECT * FROM todo WHERE id = ? AND family_id = ? AND deleted_at IS NULL",
            (current, family_id),
        ).fetchone()
        if parent is None:
            break
        stats = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN status = 1 THEN 1 ELSE 0 END) AS done "
            "FROM todo WHERE parent_id = ? AND family_id = ? AND deleted_at IS NULL",
            (current, family_id),
        ).fetchone()
        total = int(stats["total"] or 0)
        done = int(stats["done"] or 0)
        if total > 0 and total == done:
            _do_complete(conn, current, by_device, _now())
            completed.append(current)
            current = parent["parent_id"]
        else:
            break
    return completed


def _reset_ancestors_incomplete(conn: sqlite3.Connection, family_id: str, parent_id: str | None) -> None:
    """把某节点的所有祖先置回「待办」。"""
    current = parent_id
    now = _now()
    while current:
        parent = conn.execute(
            "SELECT id, parent_id FROM todo WHERE id = ? AND family_id = ? "
            "AND deleted_at IS NULL",
            (current, family_id),
        ).fetchone()
        if parent is None:
            break
        conn.execute(
            "UPDATE todo SET status = 0, status_detail = ?, completed_at = NULL, updated_at = ? "
            "WHERE id = ?",
            (STATUS_PENDING, now, current),
        )
        current = parent["parent_id"]
