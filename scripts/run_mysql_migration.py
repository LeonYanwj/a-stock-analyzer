"""在目标 MySQL 上执行一条受版本保护的增量迁移。

本脚本从标准输入读取 SQL，连接信息仅从目标环境的 config.py 读取，
不会输出密码。线上执行过的迁移由 schema_migration 记录，内容变化时拒绝重跑。
"""
import hashlib
import sys

import pymysql
from pymysql.constants import CLIENT

from config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME


def main():
    if len(sys.argv) != 4:
        raise SystemExit("用法: run_mysql_migration.py <migration_id> <checksum> <description>")

    migration_id, expected_checksum, description = sys.argv[1:]
    sql = sys.stdin.read()
    actual_checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    if actual_checksum != expected_checksum:
        raise SystemExit("迁移文件校验和不一致，拒绝执行")

    conn = pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, charset="utf8mb4", autocommit=False,
        client_flag=CLIENT.MULTI_STATEMENTS,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS schema_migration ("
                "migration_id VARCHAR(128) PRIMARY KEY, checksum CHAR(64) NOT NULL, "
                "description VARCHAR(255) NOT NULL, "
                "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
            )
            cur.execute("SELECT checksum FROM schema_migration WHERE migration_id=%s", (migration_id,))
            existing = cur.fetchone()
            if existing:
                if existing["checksum"] != expected_checksum:
                    raise RuntimeError("同名迁移内容已变更，拒绝执行")
                print({"status": "already_applied", "migration_id": migration_id})
            else:
                cur.execute(sql)
                while cur.nextset():
                    pass
                cur.execute(
                    "INSERT INTO schema_migration(migration_id, checksum, description) VALUES (%s,%s,%s)",
                    (migration_id, expected_checksum, description),
                )
                print({"status": "applied", "migration_id": migration_id})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
