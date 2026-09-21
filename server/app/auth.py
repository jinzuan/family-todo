"""家庭令牌 / 设备令牌：生成、哈希存储、join 流程、请求鉴权。

安全底线（第 4 节）：
- 明文只出现一次，库中只存 sha256 哈希；
- 比对用 hmac.compare_digest，防时序侧信道；
- WebSocket 走首帧鉴权，令牌不进 URL。
"""

import hashlib
import hmac
import secrets
import sqlite3
import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status

from . import db
from .utils.time import utcnow_iso


class AuthError(Exception):
    """鉴权失败（无效/已吊销令牌等）。"""


@dataclass
class AuthContext:
    token_id: str
    family_id: str
    device_id: str | None
    role: str
    kind: str


def hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def generate_token() -> tuple[str, str]:
    """生成令牌，返回 (明文, 哈希)。明文只返回一次。"""
    plain = secrets.token_urlsafe(32)
    return plain, hash_token(plain)


def create_family(conn: sqlite3.Connection, name: str = "我的家") -> str:
    """新建家庭，返回 family_id。"""
    family_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO family(id, name, created_at) VALUES(?, ?, ?)",
        (family_id, name, utcnow_iso()),
    )
    return family_id


def create_family_token(
    conn: sqlite3.Connection,
    family_id: str,
    role: str = "executor",
    label: str | None = "家庭入内链接",
) -> str:
    """为家庭生成一条家庭令牌，返回明文（数据库只存哈希）。"""
    plain, token_hash = generate_token()
    conn.execute(
        """INSERT INTO token(id, family_id, device_id, kind, role, token_hash,
                             label, created_at)
           VALUES(?, ?, NULL, 'family', ?, ?, ?, ?)""",
        (str(uuid.uuid4()), family_id, role, token_hash, label, utcnow_iso()),
    )
    return plain


def join(
    conn: sqlite3.Connection,
    family_token: str,
    device_name: str | None = None,
    role: str | None = None,
) -> dict:
    """用家庭令牌加入：校验 → 建/复用设备 → 发设备令牌。"""
    token_hash = hash_token(family_token)
    row = conn.execute(
        "SELECT * FROM token WHERE token_hash = ? AND kind = 'family'",
        (token_hash,),
    ).fetchone()
    # 即便查不到也用 compare_digest 走一遍，行为统一
    if row is None or not hmac.compare_digest(row["token_hash"], token_hash):
        raise AuthError("家庭令牌无效")
    if row["revoked_at"]:
        raise AuthError("家庭令牌已吊销")

    family_id = row["family_id"]
    device_role = role or row["role"] or "executor"
    device_id = str(uuid.uuid4())
    now = utcnow_iso()
    conn.execute(
        """INSERT INTO device(id, family_id, name, role, created_at, last_seen)
           VALUES(?, ?, ?, ?, ?, ?)""",
        (device_id, family_id, device_name, device_role, now, now),
    )

    plain, token_hash_new = generate_token()
    conn.execute(
        """INSERT INTO token(id, family_id, device_id, kind, role, token_hash,
                             label, created_at)
           VALUES(?, ?, ?, 'device', ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()), family_id, device_id, device_role,
            token_hash_new, device_name, now,
        ),
    )
    conn.execute(
        "UPDATE token SET last_used_at = ? WHERE id = ?",
        (now, row["id"]),
    )
    return {
        "device_token": plain,
        "device_id": device_id,
        "family_id": family_id,
        "role": device_role,
    }


def authenticate(conn: sqlite3.Connection, plain: str) -> AuthContext:
    """校验设备/家庭令牌，返回鉴权上下文，并刷新 last_used/last_seen。"""
    if not plain:
        raise AuthError("缺少令牌")
    token_hash = hash_token(plain)
    row = conn.execute(
        "SELECT * FROM token WHERE token_hash = ?", (token_hash,)
    ).fetchone()
    if row is None or not hmac.compare_digest(row["token_hash"], token_hash):
        raise AuthError("令牌无效")
    if row["revoked_at"]:
        raise AuthError("令牌已吊销")

    now = utcnow_iso()
    conn.execute("UPDATE token SET last_used_at = ? WHERE id = ?", (now, row["id"]))
    if row["device_id"]:
        conn.execute(
            "UPDATE device SET last_seen = ? WHERE id = ?", (now, row["device_id"])
        )
    return AuthContext(
        token_id=row["id"],
        family_id=row["family_id"],
        device_id=row["device_id"],
        role=row["role"],
        kind=row["kind"],
    )


def get_db():
    """FastAPI 依赖：请求级数据库连接（同一请求内复用）。"""
    with db.get_conn() as conn:
        yield conn


def require_auth(
    authorization: str | None = Header(default=None),
    conn: sqlite3.Connection = Depends(get_db),
) -> AuthContext:
    """FastAPI 依赖：解析 Authorization: Bearer <设备令牌> 并校验。"""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 Bearer 令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    plain = authorization.split(" ", 1)[1].strip()
    try:
        return authenticate(conn, plain)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )


def revoke_token(conn: sqlite3.Connection, token_id: str) -> None:
    conn.execute(
        "UPDATE token SET revoked_at = ? WHERE id = ?", (utcnow_iso(), token_id)
    )


def rotate_device_token(conn: sqlite3.Connection, ctx: AuthContext) -> str:
    """轮换当前设备令牌：吊销旧令牌，签发新令牌。"""
    if not ctx.device_id:
        raise AuthError("仅设备令牌可轮换")
    revoke_token(conn, ctx.token_id)
    plain, token_hash = generate_token()
    now = utcnow_iso()
    conn.execute(
        """INSERT INTO token(id, family_id, device_id, kind, role, token_hash,
                             label, created_at)
           VALUES(?, ?, ?, 'device', ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()), ctx.family_id, ctx.device_id, ctx.role,
            token_hash, "轮换", now,
        ),
    )
    return plain
