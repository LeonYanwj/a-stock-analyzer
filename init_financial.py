"""初始化 market_financial（同花顺财务摘要 全市场）

用法:
    python init_financial.py --limit 2000     # 主板前 2000 只
    python init_financial.py --limit 500      # 试跑
"""
import sys
import io
import argparse
import time

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from data.fetcher import DataFetcher
from universe import filter_main_board


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=2000)
    args = parser.parse_args()

    fetcher = DataFetcher()
    spot = fetcher.get_market_snapshot()
    raw = spot[["ts_code", "symbol", "name"]].copy()
    raw["list_date"] = ""
    universe = filter_main_board(raw, exclude_st=True, min_list_days=0)
    if args.limit > 0:
        universe = universe.head(args.limit)
    ts_codes = universe["ts_code"].tolist()
    print(f"目标: {len(ts_codes)} 只主板股票")

    t0 = time.time()
    total_rows = 0
    fail = 0
    n = len(ts_codes)
    for i, tc in enumerate(ts_codes, 1):
        try:
            df = fetcher.get_stock_financial_abstract(tc)
            if df is not None and not df.empty:
                total_rows += len(df)
            else:
                fail += 1
        except Exception as e:
            fail += 1
            if fail <= 3:
                print(f"  [warn] {tc}: {type(e).__name__}: {str(e)[:60]}")
        if i % 50 == 0 or i == n:
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (n - i) / rate if rate > 0 else 0
            print(f"  [{i}/{n}]  累计 {total_rows} 行  失败 {fail}  "
                  f"耗时 {elapsed:.0f}s  ETA {eta:.0f}s", flush=True)

    print(f"\n[done] 共 {total_rows:,} 行财务历史入库, 失败 {fail}, "
          f"耗时 {(time.time()-t0)/60:.1f} 分钟")


if __name__ == "__main__":
    main()
