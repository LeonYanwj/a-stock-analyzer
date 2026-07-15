"""自选股数据访问。"""
import re
from typing import Optional

import pandas as pd

from data.db import get_conn
from data.fetcher import symbol_to_ts_code

_TABLE_READY = False


def ensure_table():
    global _TABLE_READY
    if _TABLE_READY:
        return
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                watch_id INT AUTO_INCREMENT PRIMARY KEY,
                ts_code VARCHAR(10) NOT NULL UNIQUE,
                name VARCHAR(50),
                group_name VARCHAR(50) NOT NULL DEFAULT '默认',
                strategy VARCHAR(50) NOT NULL DEFAULT 'swing',
                note VARCHAR(500),
                is_active TINYINT NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_watch_active (is_active, group_name)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.close()
    _TABLE_READY = True


def normalize_code(code: str) -> str:
    value = str(code).strip().upper()
    if "." in value:
        if not re.fullmatch(r"\d{6}\.(SH|SZ)", value):
            raise ValueError("股票代码格式应为 6 位数字或 000001.SZ/600000.SH")
        return value
    if not re.fullmatch(r"\d{1,6}", value):
        raise ValueError("股票代码格式应为 6 位数字")
    return symbol_to_ts_code(value.zfill(6))


def add(code: str, name: Optional[str] = None, group_name: str = "默认",
        strategy: str = "swing", note: Optional[str] = None) -> int:
    ensure_table()
    ts_code = normalize_code(code)
    if not name:
        with get_conn() as conn:
            row = pd.read_sql(
                "SELECT name FROM market_stock_basic WHERE ts_code=%s LIMIT 1",
                conn, params=(ts_code,))
        name = None if row.empty else row.iloc[0]["name"]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO watchlist (ts_code,name,group_name,strategy,note,is_active) "
            "VALUES (%s,%s,%s,%s,%s,1) "
            "ON DUPLICATE KEY UPDATE name=VALUES(name), group_name=VALUES(group_name), "
            "strategy=VALUES(strategy), note=VALUES(note), is_active=1",
            (ts_code, name, group_name or "默认", strategy, note))
        watch_id = cur.lastrowid
        if not watch_id:
            cur.execute("SELECT watch_id FROM watchlist WHERE ts_code=%s", (ts_code,))
            watch_id = cur.fetchone()[0]
        cur.close()
    return int(watch_id)


def list_all(active_only: bool = True) -> pd.DataFrame:
    ensure_table()
    where = "WHERE w.is_active=1" if active_only else ""
    with get_conn() as conn:
        return pd.read_sql(
            "SELECT w.watch_id,w.ts_code,COALESCE(w.name,b.name) AS name,"
            "w.group_name,w.strategy,w.note,w.is_active,w.created_at,w.updated_at "
            "FROM watchlist w LEFT JOIN market_stock_basic b ON b.ts_code=w.ts_code "
            f"{where} ORDER BY w.group_name,w.watch_id", conn)


def update(watch_id: int, **fields) -> bool:
    ensure_table()
    allowed = {"name", "group_name", "strategy", "note", "is_active"}
    values = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not values:
        return False
    assignments = ",".join(f"{key}=%s" for key in values)
    params = list(values.values()) + [watch_id]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE watchlist SET {assignments} WHERE watch_id=%s", params)
        changed = cur.rowcount > 0
        if not changed:
            cur.execute("SELECT 1 FROM watchlist WHERE watch_id=%s", (watch_id,))
            changed = cur.fetchone() is not None
        cur.close()
    return changed


def delete(watch_id: int) -> bool:
    ensure_table()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM watchlist WHERE watch_id=%s", (watch_id,))
        changed = cur.rowcount > 0
        cur.close()
    return changed
