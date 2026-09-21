"""埋点统计（临时功能，可整体摘除，第 5 节）。

只存计数与聚合值，绝不存原始文本内容。
指标：publish_oneline_count / publish_manual_count / oneline_items_total /
      oneline_items_max / subtask_expand_count。
"""

import sqlite3

from .. import config
from ..utils.time import period_utc, today_utc
from datetime import datetime, timedelta, timezone

# 集中定义指标，便于评估去留
METRICS = (
    "publish_oneline_count",
    "publish_manual_count",
    "oneline_items_total",
    "oneline_items_max",
    "subtask_expand_count",
)


def _is_max_metric(metric: str) -> bool:
    return metric.endswith("_max")


def record_event(
    conn: sqlite3.Connection, family_id: str, metric: str, value: int = 1
) -> None:
    """事件发生即 UPSERT 当天桶。_max 类取最大值，其余累加。"""
    if value is None:
        value = 1
    day = today_utc()
    if _is_max_metric(metric):
        conn.execute(
            """INSERT INTO stats_daily(day, family_id, metric, count)
               VALUES(?, ?, ?, ?)
               ON CONFLICT(day, family_id, metric)
               DO UPDATE SET count = MAX(count, excluded.count)""",
            (day, family_id, metric, int(value)),
        )
    else:
        conn.execute(
            """INSERT INTO stats_daily(day, family_id, metric, count)
               VALUES(?, ?, ?, ?)
               ON CONFLICT(day, family_id, metric)
               DO UPDATE SET count = count + excluded.count""",
            (day, family_id, metric, int(value)),
        )


def record_publish(
    conn: sqlite3.Connection, family_id: str, source: str, item_count: int
) -> None:
    """记录一次发布：区分一句话/逐条，并累计拆出条数。"""
    if source == "oneline":
        record_event(conn, family_id, "publish_oneline_count", 1)
        record_event(conn, family_id, "oneline_items_total", item_count)
        record_event(conn, family_id, "oneline_items_max", item_count)
    else:
        record_event(conn, family_id, "publish_manual_count", 1)


def _aggregate(conn: sqlite3.Connection, table: str, family_id: str) -> dict[str, int]:
    """按指标聚合一张表：普通指标求和，_max 指标取最大。"""
    rows = conn.execute(
        f"SELECT metric, SUM(count) AS total, MAX(count) AS peak "
        f"FROM {table} WHERE family_id = ? GROUP BY metric",
        (family_id,),
    ).fetchall()
    result: dict[str, int] = {}
    for row in rows:
        if _is_max_metric(row["metric"]):
            result[row["metric"]] = int(row["peak"] or 0)
        else:
            result[row["metric"]] = int(row["total"] or 0)
    return result


def get_summary(conn: sqlite3.Connection, family_id: str) -> dict:
    """读取统计摘要：现存日桶 + 长期汇总，现算平均，不存比值。"""
    values = _aggregate(conn, "stats_summary", family_id)
    for metric, count in _aggregate(conn, "stats_daily", family_id).items():
        if _is_max_metric(metric):
            values[metric] = max(values.get(metric, 0), count)
        else:
            values[metric] = values.get(metric, 0) + count

    oneline_count = values.get("publish_oneline_count", 0)
    items_total = values.get("oneline_items_total", 0)
    avg = round(items_total / oneline_count, 2) if oneline_count else 0.0
    return {
        "metrics": {m: values.get(m, 0) for m in METRICS},
        "avg_oneline_items": avg,
    }


def rollup(conn: sqlite3.Connection, retention_days: int | None = None) -> int:
    """滚动汇总：把超期日桶并入 stats_summary，再删除超期明细。返回删除行数。"""
    if retention_days is None:
        retention_days = config.stats_retention_days()
    if retention_days < 0:
        return 0
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=retention_days)
    ).strftime("%Y-%m-%d")

    rows = conn.execute(
        """SELECT family_id, metric, substr(day, 1, 7) AS period,
                  SUM(count) AS total, MAX(count) AS peak
           FROM stats_daily
           WHERE day < ?
           GROUP BY family_id, metric, period""",
        (cutoff,),
    ).fetchall()
    for row in rows:
        value = int(row["peak"] or 0) if _is_max_metric(row["metric"]) else int(row["total"] or 0)
        if _is_max_metric(row["metric"]):
            conn.execute(
                """INSERT INTO stats_summary(family_id, metric, period, count)
                   VALUES(?, ?, ?, ?)
                   ON CONFLICT(family_id, metric, period)
                   DO UPDATE SET count = MAX(count, excluded.count)""",
                (row["family_id"], row["metric"], row["period"], value),
            )
        else:
            conn.execute(
                """INSERT INTO stats_summary(family_id, metric, period, count)
                   VALUES(?, ?, ?, ?)
                   ON CONFLICT(family_id, metric, period)
                   DO UPDATE SET count = count + excluded.count""",
                (row["family_id"], row["metric"], row["period"], value),
            )
    cur = conn.execute("DELETE FROM stats_daily WHERE day < ?", (cutoff,))
    return cur.rowcount or 0


def reset(conn: sqlite3.Connection, family_id: str) -> None:
    """一键清零（配置层面允许临时功能整体下线）。"""
    conn.execute("DELETE FROM stats_daily WHERE family_id = ?", (family_id,))
    conn.execute("DELETE FROM stats_summary WHERE family_id = ?", (family_id,))
