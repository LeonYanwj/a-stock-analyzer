"""数据库初始化脚本：灌入 stock_basic + 选定股票池的 valuation

用法:
    python init_data.py                    # 默认主板 500 只
    python init_data.py --limit 100        # 只灌 100 只
    python init_data.py --only-basic       # 只灌 stock_basic（快）
    python init_data.py --only-valuation   # 跳过 stock_basic，只灌估值
"""
import sys
import io
import time
import argparse

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import pandas as pd

from data.fetcher import DataFetcher
from data.db import get_conn, upsert_stock_basic, upsert_valuation
from universe import filter_main_board


# ----------------------------------------------------------------------
def init_stock_basic(fetcher: DataFetcher) -> int:
    """拉全市场 spot 并写入 market_stock_basic"""
    print("[1] 拉取全市场 spot 快照（用于 stock_basic）...")
    spot = fetcher.get_market_snapshot()
    print(f"  spot: {len(spot)} 只")

    # 准备 stock_basic 数据
    df = spot[["ts_code", "symbol", "name"]].copy()
    df["industry"] = ""
    df["area"] = ""
    df["list_date"] = None
    df["delist_date"] = None
    df["is_active"] = 1
    # 含 ST 标记（包括 *ST 和 退）
    name_upper = df["name"].astype(str).str.upper()
    df["is_st"] = (name_upper.str.contains("ST") | df["name"].str.contains("退")).astype(int)

    with get_conn() as conn:
        n = upsert_stock_basic(conn, df)
    print(f"  写入 market_stock_basic: {n} 行（含 {int(df['is_st'].sum())} 只 ST）")
    return n


def init_valuation(fetcher: DataFetcher, limit: int = 500) -> int:
    """对主板前 N 只股票拉 PE/PB 历史"""
    print(f"\n[2] 初始化 PE/PB 历史（主板前 {limit} 只）...")
    spot = fetcher.get_market_snapshot()
    raw = spot[["ts_code", "symbol", "name"]].copy()
    raw["list_date"] = ""
    universe = filter_main_board(raw, exclude_st=True, min_list_days=0)
    if limit > 0:
        universe = universe.head(limit)
    ts_codes = universe["ts_code"].tolist()
    print(f"  股票池: {len(ts_codes)} 只")

    total_rows = 0
    fail = 0
    t0 = time.time()
    n = len(ts_codes)
    for i, tc in enumerate(ts_codes, 1):
        try:
            df = fetcher.get_stock_indicator(tc)
            if df is not None and not df.empty:
                total_rows += len(df)
        except Exception as e:
            fail += 1
            if fail <= 3:
                print(f"  [warn] {tc}: {type(e).__name__}: {str(e)[:60]}")
        if i % 50 == 0 or i == n:
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (n - i) / rate if rate > 0 else 0
            print(f"  [{i}/{n}]  已拉 {total_rows} 行  失败 {fail}  "
                  f"耗时 {elapsed:.0f}s  ETA {eta:.0f}s", flush=True)

    return total_rows


def main():
    parser = argparse.ArgumentParser(description="初始化 stock_basic + valuation")
    parser.add_argument("--limit", type=int, default=500, help="估值预热的股票数量")
    parser.add_argument("--only-basic", action="store_true", help="只灌 stock_basic")
    parser.add_argument("--only-valuation", action="store_true", help="只灌 valuation")
    args = parser.parse_args()

    print("=" * 60)
    print("MySQL 数据初始化")
    print("=" * 60)

    fetcher = DataFetcher()

    if not args.only_valuation:
        init_stock_basic(fetcher)

    if not args.only_basic:
        n = init_valuation(fetcher, limit=args.limit)
        print(f"\n  累计入库 PE/PB 行数: {n:,}")

    print("\n[done] 初始化完成")


if __name__ == "__main__":
    main()
