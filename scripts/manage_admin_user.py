#!/usr/bin/env python3
"""初始化或修改唯一管理员账户；明文密码只从终端输入，不写入文件或日志。"""
import getpass
import sys

from api.passwords import hash_password
from data.db import get_conn


def main():
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        raise SystemExit("用法: python scripts/manage_admin_user.py <用户名>")

    username = sys.argv[1].strip()
    password = getpass.getpass("管理员密码: ")
    confirmation = getpass.getpass("再次输入密码: ")
    if password != confirmation:
        raise SystemExit("两次输入的密码不一致")
    if len(password) < 12:
        raise SystemExit("密码至少应包含 12 个字符")

    password_hash = hash_password(password)
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO admin_user(user_id, singleton, username, password_hash) VALUES (1, 1, %s, %s) "
                "ON DUPLICATE KEY UPDATE username=VALUES(username), "
                "password_hash=VALUES(password_hash)",
                (username, password_hash),
            )
            conn.commit()
        finally:
            cur.close()
    print("管理员账户已保存")


if __name__ == "__main__":
    main()
