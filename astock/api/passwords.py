"""管理员密码的安全哈希与验证。

数据库只保存 PBKDF2-SHA256 派生值和随机盐，不保存可恢复的明文密码。
"""
import base64
import hashlib
import hmac
import os


PASSWORD_HASH_SCHEME = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 600_000
PASSWORD_SALT_BYTES = 16


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii"))


def hash_password(password: str) -> str:
    """返回适合存入数据库的随机加盐密码哈希。"""
    salt = os.urandom(PASSWORD_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_HASH_ITERATIONS
    )
    return "$".join((
        PASSWORD_HASH_SCHEME,
        str(PASSWORD_HASH_ITERATIONS),
        _encode(salt),
        _encode(digest),
    ))


def verify_password(password: str, password_hash: str) -> bool:
    """验证密码；格式非法或不匹配均返回 False。"""
    try:
        scheme, iterations, encoded_salt, expected = password_hash.split("$", 3)
        if scheme != PASSWORD_HASH_SCHEME:
            return False
        derived = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            _decode(encoded_salt),
            int(iterations),
        )
        return hmac.compare_digest(_encode(derived), expected)
    except (TypeError, ValueError, UnicodeError):
        return False
