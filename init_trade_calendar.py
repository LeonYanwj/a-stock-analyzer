"""初始化交易日历 → market_trade_calendar

数据源: AKShare tool_trade_date_hist_sina (新浪)
覆盖范围: 1990-至今所有 A 股交易日

用法:
    python init_trade_calendar.py
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import os
os.environ.setdefault("NO_PROXY", "*")

import pandas as pd
import akshare as ak

from data.db import get_conn, upsert_df


def main():
    print("[1] 拉取交易日历（新浪）...")
    df = ak.tool_trade_date_hist_sina()
    print(f"  共 {len(df)} 个交易日")

    df = df.rename(columns={"trade_date": "cal_date"})
    df["cal_date"] = pd.to_datetime(df["cal_date"]).dt.strftime("%Y-%m-%d")
    df["is_open"] = 1
    df["exchange"] = "SSE"

    print(f"\n[2] 写入 market_trade_calendar...")
    with get_conn() as conn:
        n = upsert_df(conn, "market_trade_calendar",
                     df[["cal_date", "is_open", "exchange"]])
    print(f"  写入 {n} 行")

    # 验证
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT MIN(cal_date), MAX(cal_date), COUNT(*) FROM market_trade_calendar")
        r = cur.fetchone()
        cur.close()
    print(f"\n[done] 交易日历: {r[0]} ~ {r[1]}, 共 {r[2]} 个交易日")


if __name__ == "__main__":
    main()
