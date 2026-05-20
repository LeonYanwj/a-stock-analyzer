"""MySQL 数据库访问层

连接 quant_data 库，封装常用的读写操作。
所有时序数据用 UPSERT（ON DUPLICATE KEY UPDATE），避免重复插入。

使用示例:
    from data.db import get_conn, upsert_daily, query_daily

    with get_conn() as conn:
        upsert_daily(conn, df)              # 写入日线
        df = query_daily(conn, "000001.SZ", "2024-01-01", "2024-06-30")
"""
import contextlib
from datetime import datetime
import pandas as pd
import pymysql

from config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME


# ----------------------------------------------------------------------
# 连接管理
# ----------------------------------------------------------------------
def get_conn(autocommit: bool = True):
    """获取 MySQL 连接

    autocommit 默认 True：避免 pymysql `with conn` 退出时 ROLLBACK 的坑。
    如果要做多语句事务，传 autocommit=False 然后用 db_session() 上下文。
    """
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        autocommit=autocommit,
        connect_timeout=10,
    )


@contextlib.contextmanager
def db_session():
    """with 语法的连接 context manager；异常时自动回滚"""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ----------------------------------------------------------------------
# 通用 UPSERT 工具
# ----------------------------------------------------------------------
def upsert_df(conn, table: str, df: pd.DataFrame, batch_size: int = 1000) -> int:
    """把 DataFrame 批量 UPSERT 到表中（ON DUPLICATE KEY UPDATE）

    Args:
        conn: pymysql 连接
        table: 表名
        df: DataFrame，列名与表字段对应
        batch_size: 每批多少行

    Returns: 实际写入/更新的行数
    """
    if df is None or df.empty:
        return 0
    cols = list(df.columns)
    placeholders = ",".join(["%s"] * len(cols))
    col_list = ",".join(f"`{c}`" for c in cols)
    update_list = ",".join(f"`{c}`=VALUES(`{c}`)" for c in cols)
    sql = (f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
           f"ON DUPLICATE KEY UPDATE {update_list}")

    rows = [tuple(None if pd.isna(v) else v for v in row) for row in df.itertuples(index=False)]
    total = 0
    cur = conn.cursor()
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        cur.executemany(sql, chunk)
        total += cur.rowcount
    cur.close()
    return total


# ----------------------------------------------------------------------
# market_daily：日线 K 线
# ----------------------------------------------------------------------
def upsert_daily(conn, df: pd.DataFrame, adjust: str = "qfq") -> int:
    """写入日线（DataFrame 需含 ts_code, trade_date, open, high, low, close 等）"""
    if df is None or df.empty:
        return 0
    cols_required = ["ts_code", "trade_date"]
    cols_optional = ["open", "high", "low", "close", "vol", "amount", "pct_chg", "turnover_rate"]
    cols_keep = [c for c in cols_required + cols_optional if c in df.columns]
    out = df[cols_keep].copy()
    out["adjust"] = adjust
    return upsert_df(conn, "market_daily", out)


def query_daily(conn, ts_code: str, start_date: str, end_date: str,
                adjust: str = "qfq") -> pd.DataFrame:
    """查日线。start_date/end_date 支持 'YYYYMMDD' 或 'YYYY-MM-DD'"""
    sd = _norm_date(start_date)
    ed = _norm_date(end_date)
    sql = ("SELECT ts_code, trade_date, open, high, low, close, vol, amount, "
           "pct_chg, turnover_rate "
           "FROM market_daily "
           "WHERE ts_code=%s AND adjust=%s AND trade_date BETWEEN %s AND %s "
           "ORDER BY trade_date ASC")
    return pd.read_sql(sql, conn, params=(ts_code, adjust, sd, ed))


def query_daily_coverage(conn, ts_code: str, adjust: str = "qfq") -> tuple:
    """查询某只股票已缓存的日期区间 (min_date, max_date)；无数据返回 (None, None)"""
    sql = ("SELECT MIN(trade_date), MAX(trade_date) FROM market_daily "
           "WHERE ts_code=%s AND adjust=%s")
    cur = conn.cursor()
    cur.execute(sql, (ts_code, adjust))
    row = cur.fetchone()
    cur.close()
    return row if row else (None, None)


# ----------------------------------------------------------------------
# market_valuation：估值
# ----------------------------------------------------------------------
def upsert_valuation(conn, df: pd.DataFrame) -> int:
    cols = [c for c in ["ts_code", "trade_date", "pe", "pe_ttm", "pb", "ps",
                        "total_mv", "circ_mv"] if c in df.columns]
    return upsert_df(conn, "market_valuation", df[cols])


# ----------------------------------------------------------------------
# market_fund_flow：资金流（按日 + 窗口）
# ----------------------------------------------------------------------
def upsert_fund_flow(conn, df: pd.DataFrame, window_label: str,
                    snapshot_date) -> int:
    if df is None or df.empty:
        return 0
    cols = [c for c in ["ts_code", "fund_inflow", "fund_outflow", "fund_net"]
            if c in df.columns]
    out = df[cols].copy()
    out["window_label"] = window_label
    out["snapshot_date"] = _norm_date(snapshot_date)
    return upsert_df(conn, "market_fund_flow", out)


# ----------------------------------------------------------------------
# market_stock_basic：股票基础信息
# ----------------------------------------------------------------------
def upsert_stock_basic(conn, df: pd.DataFrame) -> int:
    cols = [c for c in ["ts_code", "symbol", "name", "industry", "area",
                        "list_date", "delist_date", "is_active", "is_st"]
            if c in df.columns]
    return upsert_df(conn, "market_stock_basic", df[cols])


def get_active_stocks(conn, only_main_board: bool = True) -> pd.DataFrame:
    """返回当前活跃股票池"""
    sql = "SELECT ts_code, symbol, name, industry, list_date FROM market_stock_basic WHERE is_active=1"
    if only_main_board:
        sql += " AND (symbol LIKE '600%' OR symbol LIKE '601%' OR symbol LIKE '603%' OR symbol LIKE '605%' OR symbol LIKE '000%' OR symbol LIKE '001%' OR symbol LIKE '002%')"
    return pd.read_sql(sql, conn)


# ----------------------------------------------------------------------
# strategy_config：策略配置
# ----------------------------------------------------------------------
def get_strategy(conn, name: str) -> dict:
    """查询策略配置"""
    sql = "SELECT * FROM strategy_config WHERE strategy_name=%s LIMIT 1"
    df = pd.read_sql(sql, conn, params=(name,))
    if df.empty:
        return None
    return df.iloc[0].to_dict()


# ----------------------------------------------------------------------
# backtest_*: 回测结果存档
# ----------------------------------------------------------------------
def create_backtest_run(conn, strategy_id: int, start_date, end_date,
                       initial_capital: float = 1.0, note: str = "") -> int:
    """新建一次回测记录，返回 run_id"""
    sql = ("INSERT INTO backtest_run (strategy_id, start_date, end_date, "
           "initial_capital, note) VALUES (%s, %s, %s, %s, %s)")
    cur = conn.cursor()
    cur.execute(sql, (strategy_id, _norm_date(start_date), _norm_date(end_date),
                     initial_capital, note))
    run_id = cur.lastrowid
    cur.close()
    return run_id


def finalize_backtest_run(conn, run_id: int, metrics: dict):
    """回测结束后更新 run 表的绩效指标"""
    sql = ("UPDATE backtest_run SET final_value=%s, total_return=%s, "
           "ann_return=%s, sharpe=%s, max_drawdown=%s, win_rate=%s, n_periods=%s "
           "WHERE run_id=%s")
    cur = conn.cursor()
    cur.execute(sql, (
        metrics.get("final_value"),
        metrics.get("total_return"),
        metrics.get("ann_return"),
        metrics.get("sharpe"),
        metrics.get("max_drawdown"),
        metrics.get("win_rate"),
        metrics.get("n_periods"),
        run_id,
    ))
    cur.close()


def insert_backtest_equity(conn, run_id: int, equity_df: pd.DataFrame) -> int:
    """写入每期净值；equity_df 需含 rebal_date, equity, period_return"""
    df = equity_df.copy()
    df["run_id"] = run_id
    cols = ["run_id", "rebal_date", "equity", "period_return"]
    df = df[[c for c in cols if c in df.columns]]
    return upsert_df(conn, "backtest_equity", df)


def insert_backtest_position(conn, run_id: int, positions_df: pd.DataFrame) -> int:
    """写入每期持仓明细；需含 rebal_date, ts_code, rank_num, weight, factor_score 等"""
    df = positions_df.copy()
    df["run_id"] = run_id
    cols = [c for c in ["run_id", "rebal_date", "ts_code", "rank_num", "weight",
                       "factor_score", "period_return", "stoploss_hit"]
            if c in df.columns]
    return upsert_df(conn, "backtest_position", df[cols])


def insert_backtest_factor_ic(conn, run_id: int, ic_df: pd.DataFrame) -> int:
    """写入每期因子 IC；需含 rebal_date, factor_name, ic"""
    df = ic_df.copy()
    df["run_id"] = run_id
    cols = ["run_id", "rebal_date", "factor_name", "ic"]
    df = df[[c for c in cols if c in df.columns]]
    return upsert_df(conn, "backtest_factor_ic", df)


def list_backtest_runs(conn, strategy_name: str = None, limit: int = 20) -> pd.DataFrame:
    """列出历史回测记录"""
    if strategy_name:
        sql = ("SELECT r.run_id, s.strategy_name, r.start_date, r.end_date, "
               "r.ann_return, r.sharpe, r.max_drawdown, r.win_rate, r.n_periods, "
               "r.note, r.created_at "
               "FROM backtest_run r JOIN strategy_config s USING(strategy_id) "
               "WHERE s.strategy_name=%s "
               "ORDER BY r.created_at DESC LIMIT %s")
        return pd.read_sql(sql, conn, params=(strategy_name, limit))
    else:
        sql = ("SELECT r.run_id, s.strategy_name, r.start_date, r.end_date, "
               "r.ann_return, r.sharpe, r.max_drawdown, r.win_rate, r.n_periods, "
               "r.note, r.created_at "
               "FROM backtest_run r JOIN strategy_config s USING(strategy_id) "
               "ORDER BY r.created_at DESC LIMIT %s")
        return pd.read_sql(sql, conn, params=(limit,))


def get_backtest_detail(conn, run_id: int) -> dict:
    """查某次回测的详细信息（持仓/净值/IC）"""
    run = pd.read_sql(
        "SELECT * FROM backtest_run WHERE run_id=%s", conn, params=(run_id,))
    if run.empty:
        return None
    equity = pd.read_sql(
        "SELECT * FROM backtest_equity WHERE run_id=%s ORDER BY rebal_date",
        conn, params=(run_id,))
    positions = pd.read_sql(
        "SELECT * FROM backtest_position WHERE run_id=%s ORDER BY rebal_date, rank_num",
        conn, params=(run_id,))
    ic = pd.read_sql(
        "SELECT * FROM backtest_factor_ic WHERE run_id=%s ORDER BY rebal_date, factor_name",
        conn, params=(run_id,))
    return {"run": run.iloc[0].to_dict(), "equity": equity,
            "positions": positions, "ic": ic}


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------
def _norm_date(d) -> str:
    """统一日期格式为 'YYYY-MM-DD'，兼容 'YYYYMMDD' 字符串或 datetime"""
    if isinstance(d, str):
        if len(d) == 8 and d.isdigit():
            return f"{d[:4]}-{d[4:6]}-{d[6:]}"
        return d
    if hasattr(d, "strftime"):
        return d.strftime("%Y-%m-%d")
    return str(d)


def health_check() -> bool:
    """健康检查：能连上 DB 且能查到 strategy_config"""
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM strategy_config")
            n = cur.fetchone()[0]
            cur.close()
            print(f"[OK] 已连上 {DB_NAME}@{DB_HOST}, 策略数量: {n}")
            return True
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")
        return False


if __name__ == "__main__":
    health_check()
