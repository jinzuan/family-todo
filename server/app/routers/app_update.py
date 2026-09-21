"""App 自更新接口：GET /api/app/latest.json（并兼容 GET /latest.json）。

返回当前最新版本信息，供安卓壳启动时检查：
    {
      "version_code": 2,              # 整数版本号，与 APK 的 versionCode 比较
      "version_name": "0.4.0",        # 展示用版本名
      "apk_url": "/downloads/xxx.apk",# 绝对或相对（相对则相对接口基址）
      "changelog": "更新说明",
      "force": false,                 # true = 强制更新（App 端弹窗不可取消）
      "min_supported_version_code": 0 # 低于此版本号视为必须更新
    }

版本受控：默认值来自环境变量，可被 server/latest.json 覆盖文件覆盖（见 config.py）。
"""

import json

from fastapi import APIRouter

from .. import config

router = APIRouter(tags=["app-update"])
root_router = APIRouter(tags=["app-update"])


def _payload() -> dict:
    payload = {
        "version_code": config.app_version_code(),
        "version_name": config.app_version_name(),
        "apk_url": config.app_apk_url(),
        "changelog": config.app_changelog(),
        "force": config.app_force_update(),
        "min_supported_version_code": config.app_min_supported_version_code(),
    }
    override = config.app_latest_json_path()
    if override.is_file():
        try:
            data = json.loads(override.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                payload.update(data)
        except (OSError, ValueError):
            # 覆盖文件损坏时回退到环境变量，不影响接口可用
            pass
    return payload


@router.get("/app/latest.json")
def latest_json():
    """App 启动检查更新用（走 /api 前缀）。"""
    return _payload()


@root_router.get("/latest.json")
def latest_json_root():
    """兼容直接访问 /latest.json（可交给 nginx 静态托管）。"""
    return _payload()
