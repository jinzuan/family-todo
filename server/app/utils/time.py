"""UTC 时间辅助：统一存 UTC 的 ISO8601 字符串。"""

from datetime import date, datetime, timezone


def utcnow_iso() -> str:
    """当前 UTC 时间，形如 2026-09-12T10:09:00Z。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_local_iso() -> str:
    """本地日历日期 YYYY-MM-DD，作为待办默认 due_date（按家庭所在时区感知）。"""
    return date.today().isoformat()


def today_utc() -> str:
    """当前 UTC 日期，形如 2026-09-12（埋点按天分桶用）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def period_utc() -> str:
    """当前 UTC 月份，形如 2026-09（滚动汇总用）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m")
