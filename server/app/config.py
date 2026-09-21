"""配置读取：全部走环境变量，带安全的默认值。

DB_PATH 相对路径以 server/ 为基准解析，保证无论从哪个工作目录启动都指向同一库。
示例值见 server/.env.example，真密钥不入库。
"""

import os
from pathlib import Path

# server/ 目录（app/ 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent


def db_path() -> Path:
    """SQLite 文件路径；相对路径基于 server/。"""
    raw = os.getenv("DB_PATH", "data/family_todo.db")
    p = Path(raw)
    return p if p.is_absolute() else BASE_DIR / p


def stats_retention_days() -> int:
    """埋点日桶保留天数；0 表示不保留明细（仅汇总）。"""
    try:
        return int(os.getenv("STATS_RETENTION_DAYS", "90"))
    except ValueError:
        return 90


def host() -> str:
    return os.getenv("HOST", "0.0.0.0")


def port() -> int:
    try:
        return int(os.getenv("PORT", "8000"))
    except ValueError:
        return 8000


# ---- App 自更新（latest.json）版本受控 ----
# 发布新版本时改环境变量并重启即可，无需改代码：
#   APP_VERSION_CODE=6 APP_VERSION_NAME=0.5.3 \
#   APP_APK_URL=/downloads/family-todo-0.5.3.apk APP_CHANGELOG="..." systemctl restart family-todo
# 也可直接放一份 server/latest.json 覆盖文件（字段同名），优先级高于环境变量。


def app_version_code() -> int:
    try:
        return int(os.getenv("APP_VERSION_CODE", "6"))
    except ValueError:
        # 环境变量写坏时退回 0（宁可不提示，也不误报新版本）
        return 0


def app_version_name() -> str:
    return os.getenv("APP_VERSION_NAME", "0.5.3")


def app_apk_url() -> str:
    # 允许相对路径（如 /downloads/xxx.apk），App 端会拼上接口地址
    return os.getenv("APP_APK_URL", "/downloads/family-todo-latest.apk")


def app_changelog() -> str:
    # 0.5.3：缩放锁定 + 待办详情可收起 + 结算弹窗 + 小组件刷新
    return os.getenv(
        "APP_CHANGELOG",
        "1. 缩放更稳：不随系统字体大小炸版，仍可双指放大\n2. 待办详情默认收起，界面更紧凑\n3. 修复今日全部完成后的结算弹窗\n4. 修复桌面小组件完成后不刷新",
    )


def app_force_update() -> bool:
    return os.getenv("APP_FORCE_UPDATE", "").strip().lower() in ("1", "true", "yes", "on")


def app_min_supported_version_code() -> int:
    try:
        return int(os.getenv("APP_MIN_SUPPORTED_VERSION_CODE", "0"))
    except ValueError:
        return 0


def app_latest_json_path() -> Path:
    """可选的覆盖文件；存在则其字段覆盖环境变量。"""
    raw = os.getenv("APP_LATEST_JSON", "latest.json")
    p = Path(raw)
    return p if p.is_absolute() else BASE_DIR / p
