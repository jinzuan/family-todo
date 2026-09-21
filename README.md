# 待会儿办（family-todo）

一个面向家庭的轻量待办工具：**发布端**用一句话或逐条发布待办，**接收端**在手机小组件上直接点一下勾掉。

> 项目处于早期阶段，功能仍在持续迭代。本文档按可自行部署的语气撰写，欢迎参考与改进。

---

## 核心特性

- **两种录入方式**
  - 一句话智能拆分：粘贴一段中文，自动按标点/换行拆成待办，识别数量、分类、价格，并把「可能补上」这类句子识别为备注而非任务。
  - 逐条细颗粒度：手动一条条添加，支持给某条待办挂**子待办**（父任务可配置为子待办全部完成才完成）。
- **实时双向同步**：发布后仍可修改，修改实时通知接收端；勾选/取消完成状态双向同步，发布端随时能看到每条完成情况。
- **默认不打扰**：发布后不主动弹提示，接收端打开应用或看小组件即可；可在设置里开启提醒，默认关闭。
- **内容与壳分离的更新策略**：界面与逻辑走服务器网页，更新即时生效；原生壳低频更新，家庭成员无需重装。
- **家庭私有空间**：凭一条含令牌的入内链接加入，零注册；仅存令牌哈希，数据留在自己的服务器上。

---

## 架构

采用**原生壳 + 网页内容**的分层结构：原生壳负责小组件、推送与防杀后台等系统能力（稳定、少更新）；界面与业务逻辑全部由网页承载，改服务器网页即完成更新，绝大多数迭代无需触碰壳。

```
┌───────── 原生壳（小组件 / 推送 / 防杀后台，低频更新）─────────┐
│  桌面小组件 · 常驻通知 · 推送通道 · WebView 容器                 │
└───────────────────────────┬──────────────────────────────────┘
                            │  http / websocket
┌───────────────────────────▼──────────────────────────────────┐
│  服务器端（更新主要在这）                                        │
│  待办存储(SQLite) · 智能拆分接口 · 家庭/设备令牌 · 推送          │
└──────────────────────────────────────────────────────────────┘
```

- 前端（网页内容）：`src/`
- 服务端（FastAPI + SQLite）：`server/`
- 实时：WebSocket 长连接 + 厂商推送

---

## 目录结构

```
family-todo/
├── src/          # 前端网页内容（界面、交互）
├── server/       # 服务端（FastAPI + SQLite、智能拆分、接口）
├── android/      # 安卓壳（WebView + 桌面小组件）
├── systemd/      # 部署示例（systemd unit）
├── LICENSE       # MIT
└── README.md
```

> 本地运行产生的 `data/`、`.env`、`server.properties` 等由 `.gitignore` 挡在库外。

---

## 快速开始 / 部署

服务端为 **FastAPI + SQLite（标准库 `sqlite3`）**，单进程单 worker，适合一台小内存服务器。

```bash
# 1. 获取代码
git clone <仓库地址>
cd family-todo

# 2. 安装依赖（建议使用虚拟环境）
python -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt

# 3. 建表（执行 server/app/schema.sql）
sqlite3 server/data/family_todo.db < server/app/schema.sql

# 4. 配置环境变量（复制示例后按需修改）
cp server/.env.example server/.env

# 5. 启动
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

环境变量示例（`server/.env.example`）：

```dotenv
DB_PATH=data/family_todo.db
STATS_RETENTION_DAYS=90
# 实际值请自行生成，不要提交到仓库
# FAMILY_TOKEN=...
```

启动后访问 `/api/health` 可做健康检查，`/docs` 为 FastAPI 自带的接口文档。

---

## 接口概览

统一前缀 `/api`，除 `join` / `health` 外均需请求头 `Authorization: Bearer <设备令牌>`。

### 认证

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/join` | 用家庭令牌加入，返回设备令牌 |
| POST | `/api/auth/refresh` | 设备令牌续期/轮换（可选） |
| GET | `/api/me` | 返回当前设备与家庭信息 |

### 智能拆分

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/parse` | 传一段文本，返回结构化拆分结果（仅预览，不落库） |

### 待办

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/todos/bulk` | 确认后批量发布（一句话拆出的多条） |
| POST | `/api/todos` | 逐条添加单条 |
| PATCH | `/api/todos/{id}` | 改标题/数量/分类/排序 |
| GET | `/api/todos?include_done=0` | 待办列表（含子待办） |
| POST | `/api/todos/{id}/complete` | 勾选完成 |
| POST | `/api/todos/{id}/uncomplete` | 取消完成 |
| DELETE | `/api/todos/{id}` | 删除（软删除） |
| POST | `/api/todos/{id}/subtasks` | 给某待办挂子待办 |
| GET | `/api/todos/{id}/subtasks` | 列出子待办 |

### 统计

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/stats/event` | 上报一次计数（只计数，不涉内容） |
| GET | `/api/stats/summary` | 查看统计摘要 |

### WebSocket

| 路径 | 说明 |
|---|---|
| `/ws` | 连接后首帧发送 `{"type":"auth","token":"..."}` 鉴权 |

推送事件类型：`todo.created` / `todo.updated` / `todo.completed` / `todo.deleted`。

更多细节见 `server/README.md`、`android/README.md`。

---

## 开源协议

本项目以 MIT 协议开源（许可条款见仓库根目录 `LICENSE`）。

由项目发起人与苏达（智能体）、opencode（AI）等协作者共同完成。

---

## 说明与免责

- 项目仍处于**早期阶段**，接口与数据结构可能调整，请以仓库最新内容为准。
- 代码与文档**仅供学习与参考**，部署到生产环境前请自行评估安全性与稳定性。
- 欢迎通过 Issue / PR 提出改进意见。
