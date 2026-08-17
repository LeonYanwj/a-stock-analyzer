"""交易管理后台的单管理员会话认证。

浏览器只在登录时提交固定管理员用户名和密码；之后使用签名、短期的
HttpOnly Cookie。保留 X-API-Key，供调度器和受控脚本继续调用交易接口。
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, Field

from api.errors import APIError
from api.passwords import verify_password


SESSION_COOKIE_NAME = "quant_trade_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
router = APIRouter(prefix="/api/auth", tags=["auth"])


class SessionLoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=512)


def _config_value(name):
    value = os.getenv(name)
    if value:
        return value
    try:
        import config
        return getattr(config, name, None)
    except ImportError:
        return None


def configured_api_key():
    return _config_value("TRADE_RUN_API_KEY")


def configured_session_secret():
    return _config_value("TRADE_RUN_SESSION_SECRET")


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _session_token(expires_at):
    secret = configured_session_secret()
    if not secret:
        raise APIError("SESSION_NOT_CONFIGURED", "服务端尚未配置 TRADE_RUN_SESSION_SECRET", 503)
    payload = {"exp": expires_at, "nonce": secrets.token_urlsafe(16)}
    encoded = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _b64(hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def session_expires_at(request):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    secret = configured_session_secret()
    if not token or not secret:
        return None
    try:
        encoded, signature = token.split(".", 1)
        expected = _b64(hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        expires_at = int(json.loads(_unb64(encoded)) ["exp"])
        return expires_at if expires_at > int(time.time()) else None
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _cookie_secure():
    """生产 HTTPS 明确设为 1；本地 Vite 默认允许非 Secure Cookie。"""
    return str(_config_value("TRADE_RUN_COOKIE_SECURE") or "").lower() in {"1", "true", "yes"}


def require_trade_run_access(request: Request, x_api_key: str | None = None):
    expected = configured_api_key()
    if expected and x_api_key and hmac.compare_digest(x_api_key, expected):
        return {"method": "api_key"}
    expires_at = session_expires_at(request)
    if expires_at:
        return {"method": "session", "expires_at": expires_at}
    raise APIError("UNAUTHORIZED", "交易管理后台需要有效登录会话或 X-API-Key", 401)


def load_admin_password_hash(username: str):
    """从管理员账户表读取密码哈希；不向浏览器泄露数据库错误细节。"""
    try:
        from data.db import get_conn
        with get_conn() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    "SELECT password_hash FROM admin_user WHERE username=%s LIMIT 1",
                    (username,),
                )
                row = cur.fetchone()
                return row[0] if row else None
            finally:
                cur.close()
    except APIError:
        raise
    except Exception as exc:
        raise APIError("LOGIN_STORAGE_UNAVAILABLE", "管理员账户服务暂不可用", 503) from exc


@router.post("/session")
def create_session(body: SessionLoginRequest, response: Response):
    password_hash = load_admin_password_hash(body.username)
    if not password_hash or not verify_password(body.password, password_hash):
        raise APIError("UNAUTHORIZED", "用户名或密码错误", 401)
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=_session_token(expires_at),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        path="/",
    )
    return {"authenticated": True, "expires_at": expires_at}


@router.get("/session")
def get_session(request: Request):
    expires_at = session_expires_at(request)
    return {"authenticated": bool(expires_at), "expires_at": expires_at}


@router.delete("/session")
def delete_session(response: Response):
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", httponly=True, samesite="lax", secure=_cookie_secure())
    return {"authenticated": False}
