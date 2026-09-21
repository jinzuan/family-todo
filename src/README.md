# 待会儿办 · 前端（M2）

零构建的网页界面：原生 HTML/CSS/JS，单页两种视图（发布端 / 执行端）。
与服务端（`server/`，FastAPI）通过 REST + WebSocket 通信，接口见 [`../server/README.md`](../server/README.md)。

## 目录

```
src/
├── index.html   # 页面骨架（加入 / 发布端 / 执行端 / 三选一弹窗）
├── styles.css   # 样式（状态色：已完成绿、已忽略灰、已取消红划线）
├── app.js       # 全部逻辑：令牌、拆分预览、发布、列表、勾选、子待办、WebSocket
├── serve.py     # 本地静态服务器（带 SPA 回退，让 /join?t=... 可用）
└── README.md
```

## 启动（开发）

先起服务端（另开终端，见 `server/README.md`）：

```bash
cd server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.cli create-family --name "我的家"   # 记下输出的 family_token
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

再起前端：

```bash
python3 src/serve.py            # 默认 http://localhost:5173/
```

打开 `http://localhost:5173/`：

- 首次进入是「加入家庭」页。把服务端的 `family_token` 填进去，选角色，点「加入」即可。
- 也可用入内链接：`http://localhost:5173/join?t=<family_token>`（`serve.py` 会回退到 `index.html`）。
- 「服务器地址」默认按当前页推断（本机 5173 端口会指向 `:8000`），可手动改成服务端地址，存本机 localStorage。
- 不想用 `serve.py` 时，任意静态服务器亦可（例如 `python -m http.server 5173`，但 `/join?t=` 路径需服务器支持回退）。

## 界面

**发布端**

- 一句话大输入框 → 「生成列表」调 `POST /api/parse` → 预览可改标题/数量/单位/金额/分类，可逐条删错；`notes` 显示在备注区（不建任务）。
- 「确认发布」调 `POST /api/todos/bulk`。
- 「逐条录入」调 `POST /api/todos`。

**执行端**

- 列表拉 `GET /api/todos?flat=1&include_done=1`，按 `parent_id` 组树，子待办可展开。
- 勾选完成调 `POST /api/todos/{id}/complete`；若返回 `needs_decision`，弹三选一：`complete`（全部完成）/ `ignore_rest`（未勾忽略）/ `cancel`（父不完成）。
- 「忽略 / 取消」调 `/ignore`、`/cancel`；「撤销」调 `/uncomplete`；「删」调 `DELETE`（软删，前端红字划线）。
- 「+子」调 `POST /api/todos/{id}/subtasks`；子项点满后父级由服务端自动完成。
- 状态色按 `status_detail`：`completed` 绿、`ignored` 灰、`cancelled` 红划线、`pending` 默认。

**实时**：进页后连 `/ws`，首帧发 `{"type":"auth","token":...}`；收到 `todo.*` 事件自动防抖刷新；服务端 `ping` 回 `pong`；断线指数退避重连并全量对齐。

## 自测

1. 起 server + `python3 src/serve.py`，浏览器打开前端。
2. 用家庭令牌加入（或点入内链接）。
3. 发布端粘贴样例：`给你姥买葱花饼15元，娃娃菜，青椒3个，回来取快递，可能后面补上` → 生成列表：应拆出 4 条待办 + 1 条备注；改一条标题，删一条 → 确认发布。
4. 执行端看到列表：勾一条变绿；给某条挂 2 个子待办后勾父级 → 弹三选一，选「未勾忽略」→ 父完成、未勾子项变灰。
5. 另开一个浏览器标签（执行端）勾选，第一个标签应经 WebSocket 自动刷新。
6. 忽略/取消/删除各点一次，确认颜色分别为灰、红划线、红划线。

## 边界

- 只改 `src/`，未动 `server/`、`README.md`。
- 页面文案与文档统一用「发布端 / 执行端 / 家庭」角色词，不含真实人名或账号。
- 令牌仅存本机 localStorage；WebSocket 令牌走首帧，不进 URL。
