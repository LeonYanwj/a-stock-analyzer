"""沪深主板多因子选股入口（AKShare 后端）

用法:
    python select.py                       # 全部主板
    python select.py --limit 200           # 只跑前 200 只（试跑）
    python select.py --top 30 --lookback 90
"""
import sys
import io
import os
import time
import argparse
from datetime import datetime, timedelta

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import pandas as pd

from data.fetcher import DataFetcher
from universe import get_universe
from factors import compute_all_factors
from selector import score, top_n


LOOKBACK_DAYS = 90
TOP_N = 50
API_SLEEP = 0.05   # AKShare 无硬限流，留极小间隔避免触发风控


def fetch_history_panel(fetcher: DataFetcher, ts_codes, start, end) -> pd.DataFrame:
    """循环拉单股历史日线，拼成长表"""
    frames, fail = [], 0
    total = len(ts_codes)
    t0 = time.time()
    for i, ts_code in enumerate(ts_codes, 1):
        df = fetcher.get_daily(ts_code, start, end)
        if df is None or df.empty:
            fail += 1
        else:
            frames.append(df)
        if i % 100 == 0 or i == total:
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (total - i) / rate if rate > 0 else 0
            print(f"  [{i}/{total}]  耗时 {elapsed:.0f}s  ETA {eta:.0f}s  失败 {fail}",
                  flush=True)
        if API_SLEEP > 0:
            time.sleep(API_SLEEP)
    if not frames:
        raise RuntimeError("没有拉到任何历史数据")
    return pd.concat(frames, ignore_index=True)


def main():
    parser = argparse.ArgumentParser(description="沪深主板多因子选股")
    parser.add_argument("--limit", type=int, default=0, help="限制股票数量（0=不限制）")
    parser.add_argument("--top", type=int, default=TOP_N, help="选股 Top N")
    parser.add_argument("--lookback", type=int, default=LOOKBACK_DAYS, help="历史回看天数")
    args = parser.parse_args()

    print("=" * 60)
    print("沪深主板多因子选股（AKShare）")
    print("=" * 60)

    fetcher = DataFetcher()
    asof_dt = datetime.now()
    asof = asof_dt.strftime("%Y%m%d")
    # 多留 30 天缓冲覆盖周末/节假日
    start = (asof_dt - timedelta(days=args.lookback + 30)).strftime("%Y%m%d")
    print(f"\n截面日: {asof}    回看起点: {start}")

    # 1. 股票池（沪深主板，排除创业板/科创板/北交所/ST）
    print("\n[1/4] 筛选沪深主板股票池...")
    universe = get_universe(fetcher, exclude_st=True, min_list_days=0)
    if args.limit > 0:
        universe = universe.head(args.limit)
    print(f"  股票池: {len(universe)} 只")

    # 2. 全市场截面快照（PE/PB/市值/换手率）
    print("\n[2/4] 获取全市场截面快照...")
    spot = fetcher.get_market_snapshot()
    print(f"  快照: {len(spot)} 只")

    # 3. 历史日线（用于动量/反转/波动率因子）
    print(f"\n[3/4] 拉取 {len(universe)} 只股票近 {args.lookback}+ 天日线...")
    panel = fetch_history_panel(fetcher, universe["ts_code"].tolist(), start, asof)
    print(f"  面板: {len(panel)} 行 × {panel['ts_code'].nunique()} 只")

    # 把 spot 截面字段 merge 到 panel（compute 时 snap 取最新日截面）
    spot_cols = [c for c in ["ts_code", "pe_ttm", "pb", "total_mv", "circ_mv"] if c in spot.columns]
    panel = panel.merge(spot[spot_cols], on="ts_code", how="left")

    # 4. 因子 + 打分
    print("\n[4/4] 计算因子 + 打分排序...")
    factors = compute_all_factors(panel, asof_date=asof)
    print(f"  因子表: {factors.shape[0]} 只 × {factors.shape[1]} 因子")
    scored = score(factors)
    picks = top_n(scored, n=args.top)

    # 5. 输出
    name_map = universe.set_index("ts_code")[["name"]]
    out = picks.join(name_map, how="left")
    cols = ["name", "score", "valid_factors",
            "ep_ttm", "bp", "mom_60", "reversal_5", "small_size", "low_vol", "liquidity"]
    out = out[[c for c in cols if c in out.columns]]

    os.makedirs("output", exist_ok=True)
    out_path = os.path.join("output", f"picks_{asof}.csv")
    out.to_csv(out_path, encoding="utf-8-sig")
    print(f"\n选股结果已保存: {out_path}")

    print("\n" + "=" * 60)
    print(f"Top {min(args.top, len(out))} 选股")
    print("=" * 60)
    pd.set_option("display.unicode.east_asian_width", True)
    pd.set_option("display.width", 200)
    print(out[["name", "score"]].round(4).to_string())


if __name__ == "__main__":
    main()
