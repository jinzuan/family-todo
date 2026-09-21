-- 《待会儿办》M1 建表 SQL
-- 单库文件 SQLite；时间统一存 UTC ISO8601 字符串；软删除用 deleted_at。

PRAGMA journal_mode = WAL;          -- 提升并发读，写仍串行
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;         -- 写锁等待 5s，避免直接报错

-- 家庭空间
CREATE TABLE IF NOT EXISTS family (
  id          TEXT PRIMARY KEY,           -- uuid4
  name        TEXT NOT NULL DEFAULT '我的家',
  created_at  TEXT NOT NULL
);

-- 设备（每台手机一行；用于推送寻址与角色区分）
CREATE TABLE IF NOT EXISTS device (
  id          TEXT PRIMARY KEY,           -- uuid4
  family_id   TEXT NOT NULL REFERENCES family(id) ON DELETE CASCADE,
  name        TEXT,                       -- 如"发布端手机""接收端手机"
  role        TEXT NOT NULL DEFAULT 'executor',  -- publisher | executor
  push_token  TEXT,                       -- 厂商推送 token（M4 用，M1 可空）
  last_seen   TEXT,
  created_at  TEXT NOT NULL
);

-- 令牌（家庭令牌 + 设备令牌统一存；只存哈希，见第 4 节）
CREATE TABLE IF NOT EXISTS token (
  id          TEXT PRIMARY KEY,           -- uuid4
  family_id   TEXT NOT NULL REFERENCES family(id) ON DELETE CASCADE,
  device_id   TEXT REFERENCES device(id) ON DELETE CASCADE,  -- 家庭令牌为 NULL
  kind        TEXT NOT NULL,              -- family | device
  role        TEXT NOT NULL DEFAULT 'executor',  -- 家庭令牌进入后发设备令牌时的默认角色
  token_hash  TEXT NOT NULL UNIQUE,       -- sha256(明文)
  label       TEXT,
  created_at  TEXT NOT NULL,
  last_used_at TEXT,
  revoked_at  TEXT
);

-- 待办（含子待办：parent_id 自引用）
CREATE TABLE IF NOT EXISTS todo (
  id            TEXT PRIMARY KEY,
  family_id     TEXT NOT NULL REFERENCES family(id) ON DELETE CASCADE,
  parent_id     TEXT REFERENCES todo(id) ON DELETE CASCADE,  -- NULL=一级
  title         TEXT NOT NULL,
  qty           REAL,                     -- 数量，可空
  unit          TEXT,                     -- 单位
  price         REAL,                     -- 金额，可空
  category      TEXT NOT NULL DEFAULT 'other',  -- shopping | errand | other
  tag           TEXT,                           -- 自动标签：动词+对象，如「给姥姥买」；可空
  important     INTEGER NOT NULL DEFAULT 0,     -- 1=重要（未完成时置顶；完成后落末尾）
  note          TEXT,
  due_date      TEXT,                           -- YYYY-MM-DD；旧字段保留兼容，新逻辑用 start/end
  start_date    TEXT,                           -- 有效期起点 YYYY-MM-DD；与 end_date 双空=持久待办
  end_date      TEXT,                           -- 有效期终点 YYYY-MM-DD；区间=有窗口，勾完即整条结束
  status        INTEGER NOT NULL DEFAULT 0,     -- 0待办 1已处理（兼容旧字段）
  status_detail TEXT NOT NULL DEFAULT 'pending',-- pending | completed | ignored | cancelled
  sort_order    INTEGER NOT NULL DEFAULT 0,
  source        TEXT NOT NULL DEFAULT 'oneline',-- oneline | manual
  created_by    TEXT REFERENCES device(id) ON DELETE SET NULL,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  completed_at  TEXT,
  completed_by  TEXT REFERENCES device(id) ON DELETE SET NULL,  -- 完成人（勾选完成那一方）
  acked_by      TEXT REFERENCES device(id) ON DELETE SET NULL,  -- 知悉人（标记知悉那一方）
  ack_at        TEXT,                                           -- 知悉时间
  deleted_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_todo_family ON todo(family_id, status, sort_order);
CREATE INDEX IF NOT EXISTS idx_todo_parent ON todo(parent_id);
-- 日期窗口/过期索引在 db._migrate 中创建：旧库需先补 start_date/end_date 列，
-- 若写在此处会在旧库 executescript 阶段因列不存在而失败。全新库同样由迁移兜底。

-- 埋点：按天分桶（天然支持滚动清零，见第 5 节）
CREATE TABLE IF NOT EXISTS stats_daily (
  day        TEXT NOT NULL,               -- YYYY-MM-DD (UTC)
  family_id  TEXT NOT NULL REFERENCES family(id) ON DELETE CASCADE,
  metric     TEXT NOT NULL,               -- 见第 5 节指标名
  count      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, family_id, metric)
);

-- 埋点：滚动汇总（保留长期摘要，日数据过期可删）
CREATE TABLE IF NOT EXISTS stats_summary (
  family_id  TEXT NOT NULL REFERENCES family(id) ON DELETE CASCADE,
  metric     TEXT NOT NULL,
  period     TEXT NOT NULL,               -- 如 2026-09
  count      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (family_id, metric, period)
);
