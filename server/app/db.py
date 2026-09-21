"""SQLite 连接与初始化。

约定：每次请求开/关一个连接（M1 单进程串行写，够用且不泄漏）；
开启 WAL / foreign_keys / busy_timeout，写锁最多等 5s。
"""

import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA_FILE = config.BASE_DIR / "app" / "schema.sql"


def connect() -> sqlite3.Connection:
    path = config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def get_conn():
    """请求级连接：正常提交、异常回滚、最终关闭。"""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """轻量迁移：给旧库补齐新列（schema.sql 的 CREATE IF NOT EXISTS 不会加列）。"""
    # 评论功能已下线：旧库平滑删除 comment 表（todo.note 是分配式附注，保留）。
    conn.execute("DROP TABLE IF EXISTS todo_comment")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(todo)")}
    if "status_detail" not in columns:
        conn.execute(
            "ALTER TABLE todo ADD COLUMN status_detail TEXT NOT NULL DEFAULT 'pending'"
        )
    if "due_date" not in columns:
        # 旧库平滑迁移：新增日期列；已有数据为 NULL，查询时视作今天
        conn.execute("ALTER TABLE todo ADD COLUMN due_date TEXT")
    if "start_date" not in columns:
        conn.execute("ALTER TABLE todo ADD COLUMN start_date TEXT")
    if "end_date" not in columns:
        conn.execute("ALTER TABLE todo ADD COLUMN end_date TEXT")
    if "important" not in columns:
        # V5：重要标记；旧库补列后默认全部非重要
        conn.execute("ALTER TABLE todo ADD COLUMN important INTEGER NOT NULL DEFAULT 0")
    if "tag" not in columns:
        # 给XX自动标签：旧库补列，历史数据为 NULL
        conn.execute("ALTER TABLE todo ADD COLUMN tag TEXT")
    if "completed_by" not in columns:
        # 反馈闭环：完成人（勾选完成那一方）
        conn.execute("ALTER TABLE todo ADD COLUMN completed_by TEXT REFERENCES device(id) ON DELETE SET NULL")
    if "acked_by" not in columns:
        # 反馈闭环：知悉人（标记知悉那一方）
        conn.execute("ALTER TABLE todo ADD COLUMN acked_by TEXT REFERENCES device(id) ON DELETE SET NULL")
    if "ack_at" not in columns:
        conn.execute("ALTER TABLE todo ADD COLUMN ack_at TEXT")
    # 一次性回填（幂等）：旧行 due_date → 单日窗口 start=end=due_date；
    # due_date 为 NULL 的极老数据保持双空=持久。
    conn.execute(
        "UPDATE todo SET start_date = due_date, end_date = due_date "
        "WHERE due_date IS NOT NULL AND start_date IS NULL AND end_date IS NULL"
    )
    # 窗口/过期索引：必须在补列之后创建，兼容旧库。
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_todo_window ON todo(family_id, start_date, end_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_todo_overdue ON todo(family_id, status, end_date)"
    )


def init_db() -> None:
    """执行 schema.sql 建表（幂等），并做轻量迁移。"""
    conn = connect()
    try:
        with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()
