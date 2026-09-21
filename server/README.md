# 待会儿办 · 服务端（M1）

FastAPI + SQLite（标准库 `sqlite3`）单进程单 worker 实现，覆盖 M1 最小闭环：
**智能拆分 + 家庭/设备令牌入内 + 待办增删勾 + 子待办父子完成 + 埋点 + WebSocket 实时推送**。

本目录属于公开发布内容。

---

## 1. 目录

```
server/
├── app/
│   ├── main.py                # FastAPI 实例、路由注册、启动建表
│   ├── config.py              # 环境变量（DB_PATH、STATS_RETENTION_DAYS）
│   ├── db.py                  # sqlite3 连接、PRAGMA、建表
│   ├── schema.sql             # 建表 SQL（WAL / foreign_keys / busy_timeout）
│   ├── schemas.py             # pydantic 请求/响应模型
│   ├── auth.py                # 令牌生成/哈希/校验、join、FastAPI 鉴权依赖
│   ├── ws_manager.py          # WebSocket 连接管理、按家庭广播
│   ├── cli.py                 # 建库 / 建家庭 / 签家庭令牌
│   ├── routers/               # auth / parse / todos / stats / ws
│   └── services/              # parser / todo_service / stats_service
├── tests/                     # test_parser.py（样例基线）+ test_api.py（冒烟）
├── requirements.txt
├── Dockerfile
├── .env.example
├── .gitignore                 # 挡 data/ .env *.db __pycache__
└── README.md
```

## 2. 运行

```bash
cd server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 建库并创建家庭，拿到家庭令牌（明文只显示一次）
python -m app.cli create-family --name "我的家"

# 启动（单 worker）
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- 健康检查：`GET /api/health`
- 接口文档：`/docs`
- 环境变量见 `.env.example`；`DB_PATH` 相对路径以 `server/` 为基准。
- 应用启动时自动建表（幂等）并做一次埋点滚动汇总。

Docker：

```bash
docker build -t family-todo-server server
docker run -p 8000:8000 -v family_todo_data:/data family-todo-server
```

## 3. 加入流程

1. 用 CLI 生成家庭令牌，拼成入内链接 `https://<域名>/join?t=<家庭令牌>`。
2. 前端 `POST /api/auth/join {family_token, device_name}` → 返回**设备令牌**（仅此一次）。
3. 之后请求带 `Authorization: Bearer <设备令牌>`。
4. WebSocket 连接 `/ws` 后**首帧**发 `{"type":"auth","token":"<设备令牌>"}`（不放 URL，避免进访问日志）。

令牌只存 `sha256` 哈希，比对用 `hmac.compare_digest`；可在库中置 `revoked_at` 吊销。

## 4. 接口一览

统一前缀 `/api`，除 `join` / `health` 外均需 `Authorization: Bearer <设备令牌>`。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/join` | 家庭令牌加入，返回设备令牌 |
| POST | `/api/auth/refresh` | 轮换设备令牌 |
| GET | `/api/me` | 当前设备/家庭信息 |
| POST | `/api/parse` | 一句话拆分（仅预览，不落库） |
| POST | `/api/todos/bulk` | 批量发布 |
| POST | `/api/todos` | 单条添加 |
| GET | `/api/todos?include_done=0&flat=0` | 列表；`flat=1` 含子待办平铺 |
| PATCH | `/api/todos/{id}` | 改标题/数量/分类/排序 |
| POST | `/api/todos/{id}/complete` | 勾选完成；父级有待办子项时返回三选一提示，可带 `{"decision":"complete\|ignore_rest\|cancel"}` 复调 |
| POST | `/api/todos/{id}/ignore` | 标为「已忽略」（灰色略过，算处理完） |
| POST | `/api/todos/{id}/cancel` | 标为「已取消」（明确不做，算处理完） |
| POST | `/api/todos/{id}/uncomplete` | 取消完成，回到「待办」 |
| DELETE | `/api/todos/{id}` | 软删除（含子孙） |
| POST | `/api/todos/{id}/subtasks` | 挂子待办 |
| GET | `/api/todos/{id}/subtasks` | 列子待办 |
| POST | `/api/stats/event` | 埋点上报 |
| GET | `/api/stats/summary` | 统计摘要 |
| GET | `/api/health` | 健康检查 |
| WS | `/ws` | 首帧鉴权，收 `todo.*` 事件 |

**待办状态**：`status`(0/1) 保留旧语义（0=待办，1=已处理），另加 `status_detail ∈ {pending, completed, ignored, cancelled}` 细分。已忽略/已取消都算处理完，默认列表（`include_done=0`）只出 `pending`，`include_done=1` 出全部。

**父子完成规则（用户 09-12 定稿）**：
1. **勾父级**：若还有「待办」子项，`complete` 不直接完成，返回 `{"needs_decision": true, "actions": ["complete","ignore_rest","cancel"], "pending_count": n}`；前端弹三选一后带 `decision` 复调：
   - `complete`：父强制完成，子项不动；
   - `ignore_rest`：父完成，未勾子项标为「已忽略」；
   - `cancel`：父不完成。
2. **子集全点满 → 自动勾父级**：子项全部为 completed/ignored/cancelled（无待办）→ 父自动完成并向上递归。
3. 取消某子项处理完成 / 给已完成父待办新挂待办子项 → 所有祖先回到「待办」。

**WebSocket 事件**：`todo.created` / `todo.updated` / `todo.completed` / `todo.deleted`；服务端每 30s 发 `ping`，客户端回 `pong`。断线重连后建议先 `GET /api/todos` 全量对齐。

**埋点指标**（只计数，绝不存原文）：`publish_oneline_count`、`publish_manual_count`、`oneline_items_total`、`oneline_items_max`、`subtask_expand_count`；日桶保留 `STATS_RETENTION_DAYS` 天（设 0 不保留明细），超期滚动进 `stats_summary`。

## 5. 测试

```bash
cd server
python -m unittest discover -s tests -v
```

- `tests/test_parser.py`：把 `M1-TECHNICAL` 第 2.5 节样例表固化为基线，另含小数/千分位保护、全角数字、整段 items/notes 分流。
- `tests/test_api.py`：使用临时库，覆盖 join/鉴权/parse/待办全生命周期/子待办父子完成/埋点/WebSocket 收发与鉴权拒绝。

## 6. 自测通过说明（2026-09-12）

- **单元测试**：`python -m unittest discover -s tests` → **29 passed**（解析 11、接口 18），全绿。
- **真实启动 + curl 全链路**（uvicorn + 临时库，非 TestClient）：
  - `GET /api/health` → `{"status":"ok"}` ✅
  - `POST /api/auth/join` → 返回 `device_token` / `device_id` / `family_id` / `role` ✅
  - `GET /api/me` ✅
  - `POST /api/parse`（样例文本）→ `给你姥买葱花饼` price=15、`青椒` qty=3、备注句进 `notes` ✅
  - `POST /api/todos/bulk` → 2 条；`GET /api/todos` 列出 ✅
  - `POST /api/todos/{id}/complete` → status=1；`/uncomplete` → status=0 ✅
  - `DELETE /api/todos/{id}` → 返回软删 id 列表 ✅
  - `POST /api/stats/event` + `GET /api/stats/summary` → 计数与平均正确 ✅
  - WebSocket `/ws`：首帧鉴权成功，随后收到另一条 HTTP 创建触发的 `todo.created` 广播 ✅
- **边界**：`server/.gitignore` 已挡 `data/`、`.env`、`*.db`；`git status` 仅 `server/`，无库/密钥外泄。

## 7. 与施工图的差异说明

1. 按第 7 节结构实现；另加 `app/cli.py` 用于建家庭/签令牌（方案未提供家庭创建接口，CLI 是最小侵入做法），FastAPI 鉴权依赖放在 `auth.py` 内，未额外建文件。
2. `GET /api/todos` 增加 `flat` 查询参数（平铺含子待办）；默认仍只列一级，符合"树形或平铺+parent_id"的要求。
3. 分句还原占位符按小数/千分位分别还原（施工图示例代码统一还原成 `.` 会把 `1,000` 变成 `1.000`），属实现层修正，不改变规则语义。
4. 为便于 M1 网页联调开启了宽松 CORS，生产应改为具体域名。
5. 父子完成规则按设计定稿实现（三选一提示 + 点满自动完成 + 已忽略/已取消）；`parent_auto` 可配字段预留、未展开。
