"""全市场每日评级快照的持久化与趋势判断。"""
import json
from datetime import date
from typing import Optional

import pandas as pd

from data.db import get_conn

_TABLE_READY = False


def ensure_table():
    global _TABLE_READY
    if _TABLE_READY:
        return
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS stock_rating_daily (
                trade_date DATE NOT NULL,
                ts_code VARCHAR(10) NOT NULL,
                strategy VARCHAR(50) NOT NULL,
                score DECIMAL(12,6),
                grade VARCHAR(2),
                rank_num INT,
                percentile DECIMAL(10,6),
                trend_state VARCHAR(12) NOT NULL DEFAULT 'unknown',
                financial_risk_level VARCHAR(12) NOT NULL DEFAULT 'unknown',
                risk_flags JSON,
                factor_values JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (trade_date, ts_code, strategy),
                INDEX idx_rating_code (ts_code, strategy, trade_date),
                INDEX idx_rating_rank (trade_date, strategy, rank_num)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.close()
    _TABLE_READY = True


def grade_from_percentile(percentile: float) -> str:
    if percentile < 0.05:
        return "S"
    if percentile < 0.20:
        return "A"
    if percentile < 0.50:
        return "B"
    if percentile < 0.80:
        return "C"
    return "D"


def derive_trend_state(row) -> str:
    """根据价格位置、动量和 MACD 给出稳定的三档趋势状态。"""
    def num(key):
        value = row.get(key)
        try:
            if value is None or pd.isna(value):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    ma20_gap = num("ma20_gap")
    ma5_gap = num("ma5_gap")
    momentum = num("mom_30")
    macd_hist = num("macd_hist")
    macd_slope = num("macd_slope")
    score = num("score")

    if all(value is None for value in (
            ma20_gap, ma5_gap, momentum, macd_hist, macd_slope, score)):
        return "unknown"

    if (ma20_gap is not None and ma20_gap < 0
            and momentum is not None and momentum < 0):
        return "bad"
    if (ma5_gap is not None and ma5_gap < 0
            and macd_hist is not None and macd_hist < 0
            and macd_slope is not None and macd_slope < 0):
        return "weak"
    if score is not None and score < 0:
        return "weak"
    return "good"


def prepare_snapshot(scored: pd.DataFrame, trade_date, strategy: str) -> pd.DataFrame:
    """把横截面分数转换成可落库的评级快照。"""
    snapshot = scored.sort_values(
        "score", ascending=False, na_position="last").copy()
    if snapshot.empty:
        return snapshot

    valid_mask = snapshot["score"].notna()
    n_valid = int(valid_mask.sum())
    snapshot["rank_num"] = pd.Series(pd.NA, index=snapshot.index, dtype="Int64")
    snapshot["percentile"] = float("nan")
    snapshot["grade"] = "N/A"
    if n_valid:
        ranks = pd.Series(range(1, n_valid + 1), index=snapshot.index[valid_mask])
        snapshot.loc[valid_mask, "rank_num"] = ranks
        percentiles = (ranks - 1) / max(n_valid, 1)
        snapshot.loc[valid_mask, "percentile"] = percentiles
        snapshot.loc[valid_mask, "grade"] = percentiles.map(grade_from_percentile)
    snapshot["trend_state"] = snapshot.apply(derive_trend_state, axis=1)
    snapshot["trade_date"] = pd.to_datetime(trade_date).date()
    snapshot["strategy"] = strategy
    if "financial_risk_level" not in snapshot.columns:
        snapshot["financial_risk_level"] = "unknown"
    if "financial_risk_flags" not in snapshot.columns:
        snapshot["financial_risk_flags"] = [[] for _ in range(len(snapshot))]
    return snapshot


def _json_value(value):
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, default=str)
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    return value


def save_snapshot(snapshot: pd.DataFrame) -> int:
    if snapshot is None or snapshot.empty:
        return 0
    ensure_table()
    meta_cols = {
        "trade_date", "strategy", "score", "grade", "rank_num", "percentile",
        "trend_state", "financial_risk_level", "financial_risk_flags", "name",
        "eligible",
    }
    sql = """
        INSERT INTO stock_rating_daily
        (trade_date, ts_code, strategy, score, grade, rank_num, percentile,
         trend_state, financial_risk_level, risk_flags, factor_values)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          score=VALUES(score), grade=VALUES(grade), rank_num=VALUES(rank_num),
          percentile=VALUES(percentile), trend_state=VALUES(trend_state),
          financial_risk_level=VALUES(financial_risk_level),
          risk_flags=VALUES(risk_flags), factor_values=VALUES(factor_values)
    """
    rows = []
    for ts_code, row in snapshot.iterrows():
        factor_values = {
            col: _json_value(row[col])
            for col in snapshot.columns
            if col not in meta_cols
        }
        rows.append((
            row["trade_date"], ts_code, row["strategy"], _json_value(row["score"]),
            row["grade"], (int(row["rank_num"]) if pd.notna(row["rank_num"]) else None),
            _json_value(row["percentile"]),
            row["trend_state"], row["financial_risk_level"],
            json.dumps(row["financial_risk_flags"] or [], ensure_ascii=False),
            json.dumps(factor_values, ensure_ascii=False, default=str),
        ))
    with get_conn() as conn:
        cur = conn.cursor()
        cur.executemany(sql, rows)
        cur.close()
    return len(rows)


def _decode_row(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    out = dict(row)
    for key in ("risk_flags", "factor_values"):
        value = out.get(key)
        if isinstance(value, str):
            try:
                out[key] = json.loads(value)
            except json.JSONDecodeError:
                out[key] = [] if key == "risk_flags" else {}
    return out


def get_latest_rating(ts_code: str, strategy: str,
                      on_or_before=None) -> Optional[dict]:
    ensure_table()
    cutoff = on_or_before or date.today()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM stock_rating_daily WHERE ts_code=%s AND strategy=%s "
            "AND trade_date<=%s ORDER BY trade_date DESC LIMIT 1",
            (ts_code, strategy, cutoff))
        row = cur.fetchone()
        columns = [d[0] for d in cur.description] if cur.description else []
        cur.close()
    return _decode_row(dict(zip(columns, row))) if row else None


def get_previous_rating(ts_code: str, strategy: str,
                        before_date) -> Optional[dict]:
    ensure_table()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM stock_rating_daily WHERE ts_code=%s AND strategy=%s "
            "AND trade_date<%s ORDER BY trade_date DESC LIMIT 1",
            (ts_code, strategy, before_date))
        row = cur.fetchone()
        columns = [d[0] for d in cur.description] if cur.description else []
        cur.close()
    return _decode_row(dict(zip(columns, row))) if row else None
