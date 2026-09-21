"""FastAPI 应用入口：路由注册、启动建表、健康检查。

启动：uvicorn app.main:app --host 0.0.0.0 --port 8000（单进程单 worker）。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config, db
from .routers import app_update, auth, parse, stats, todos, ws
from .services import stats_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 建表（幂等）+ 启动时滚动汇总一次埋点
    db.init_db()
    with db.get_conn() as conn:
        stats_service.rollup(conn)
    yield


app = FastAPI(title="待会儿办 API", version="0.1.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """请求体校验失败统一返回 400（含 start_date > end_date 等业务校验）。"""
    return JSONResponse(status_code=400, content={"detail": "请求参数不合法"})

# M1 联调放开跨域；生产按实际域名收紧
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (auth.router, parse.router, todos.router, stats.router):
    app.include_router(router, prefix="/api")

# App 自更新：/api/app/latest.json；/latest.json 兼容别名（在静态 mount 之前注册）
app.include_router(app_update.router, prefix="/api")
app.include_router(app_update.root_router)

# WebSocket 固定路径 /ws
app.include_router(ws.router)


@app.get("/api/health", tags=["health"])
def health():
    """健康检查（监控/部署用，免鉴权）。"""
    return {"status": "ok"}


@app.middleware("http")
async def no_store_static(request, call_next):
    """静态文件禁用缓存，保证改动即时生效（浏览器每次重新拉取）。"""
    resp = await call_next(request)
    path = request.url.path
    if path.endswith((".css", ".js", ".html")) or path.endswith("/") or path in ("/index.html",):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


# ---- 同源部署：后端托管前端静态（省内存、免跨域）----
# 前端在 server/ 上一层的 src/；后端 8000 一个进程同时喂页面+API。
# app.js 会自动用 window.location.origin（即 <域名>:8000），WS 同源。
# 注意：/join 与所有 API/WS 路由都在此 mount 之前注册，mount("/") 只作静态兜底。
import os

from fastapi.responses import FileResponse

_FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "src")


@app.get("/join")
def join_index():
    # SPA 回退：/join?t=xxx 也返回前端页（由 app.js 解析 t）
    return FileResponse(os.path.join(_FRONTEND_DIR, "index.html"))


# ---- 静态兜底：必须放在所有 API/WS/join 路由之后注册 ----
# mount("/") 匹配未命中的路径；/api /ws /join 已在前面精确注册，不受影响。
from fastapi.staticfiles import StaticFiles

if os.path.isdir(_FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="static")
