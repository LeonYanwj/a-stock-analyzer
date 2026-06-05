"""实盘持仓记录（与模拟盘 paper_* 完全隔离）

只存"我真实持有什么"。盘后由 holding_analyzer 对这些股票做全方位分析并邮件推送。
持仓数据由前端通过 /api/holdings 接口录入/维护。

表 real_holding 在模块导入时自动建（CREATE TABLE IF NOT EXISTS），无需手动跑 schema。
"""
import pandas as pd
from data.db import get_conn


_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS real_holding (
    holding_id  INT AUTO_INCREMENT PRIMARY KEY,
    ts_code     VARCHAR(12) NOT NULL UNIQUE,
    name        VARCHAR(32),
    qty         INT NOT NULL,
    cost        DECIMAL(12,3) NOT NULL,
    buy_date    DATE,
    note        VARCHAR(255),
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) CHARACTER SET utf8mb4
"""


def ensure_table():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(_TABLE_SQL)
        cur.close()


def _norm(code: str) -> str:
    """统一成带后缀的 ts_code（如 600036 -> 600036.SH）"""
    from rate import normalize_code
    return normalize_code(code)


def _lookup_name(ts_code: str):
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM market_stock_basic WHERE ts_code=%s", (ts_code,))
            row = cur.fetchone()
            cur.close()
        return row[0] if row else None
    except Exception:
        return None


def add_holding(code, qty, cost, buy_date=None, name=None, note=None) -> int:
    """新增/更新一只持仓（同一 ts_code 已存在则更新）。返回 holding_id。"""
    ts_code = _norm(code)
    if not name:
        name = _lookup_name(ts_code)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO real_holding (ts_code,name,qty,cost,buy_date,note) "
            "VALUES (%s,%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE qty=VALUES(qty), cost=VALUES(cost), "
            "buy_date=VALUES(buy_date), name=VALUES(name), note=VALUES(note)",
            (ts_code, name, int(qty), float(cost), buy_date, note))
        hid = cur.lastrowid
        cur.close()
    return hid


def list_holdings() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql(
            "SELECT holding_id, ts_code, name, qty, cost, buy_date, note "
            "FROM real_holding ORDER BY holding_id", conn)


def get_holding(holding_id: int) -> dict:
    with get_conn() as conn:
        df = pd.read_sql(
            "SELECT holding_id, ts_code, name, qty, cost, buy_date, note "
            "FROM real_holding WHERE holding_id=%s", conn, params=(holding_id,))
    return None if df.empty else df.iloc[0].to_dict()


def update_holding(holding_id: int, **fields) -> bool:
    """部分更新；只接受 qty/cost/buy_date/note/name。"""
    allowed = ("qty", "cost", "buy_date", "note", "name")
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not sets:
        return False
    cols = ", ".join(f"{k}=%s" for k in sets)
    vals = list(sets.values()) + [holding_id]
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE real_holding SET {cols} WHERE holding_id=%s", vals)
        n = cur.rowcount
        cur.close()
    return n > 0


def delete_holding(holding_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM real_holding WHERE holding_id=%s", (holding_id,))
        n = cur.rowcount
        cur.close()
    return n > 0


# 模块导入即确保表存在（DB 不可用时静默，不阻断导入）
try:
    ensure_table()
except Exception:
    pass
