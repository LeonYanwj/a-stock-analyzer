"""个股多维度评级入口（独立于 screen.py）

用法:
    python rate.py 600487                    # 评级指定股票（自动补全交易所）
    python rate.py 600487.SH 000001.SZ       # 多只
    python rate.py --top 30                  # 全市场跑完输出 Top 30（按综合分排序）
    python rate.py --all                     # 全市场全部输出

评级是横截面概念：内部总是以全市场主板（约 3000 只）为参照计算分位数，
即使你只评几只股票，它们的等级也是相对于全市场的位置。

输出: output/rating_YYYYMMDD.csv
"""
import sys
import io
import os
import argparse
from datetime import datetime, timedelta

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import pandas as pd

from data.fetcher import DataFetcher
from universe import get_universe
from factors import compute_all_factors
from grader import grade_all, DIMENSIONS
# 复用 screen.py 的拉数据函数（避免重复实现）
from screen import fetch_history_panel, LOOKBACK_DAYS


def _normalize_code(code: str) -> str:
    """600487 -> 600487.SH; 000001 -> 000001.SZ; 已带后缀的原样"""
    code = code.strip().upper()
    if "." in code:
        return code
    s = code.zfill(6)
    if s.startswith(("6", "9")):
        return f"{s}.SH"
    return f"{s}.SZ"


def main():
    parser = argparse.ArgumentParser(description="A股多维度评级")
    parser.add_argument("codes", nargs="*", help="股票代码（如 600487 或 600487.SH）")
    parser.add_argument("--top", type=int, default=0, help="全市场跑完取 Top N")
    parser.add_argument("--all", action="store_true", help="输出全市场评级")
    parser.add_argument("--lookback", type=int, default=LOOKBACK_DAYS, help="历史回看天数")
    args = parser.parse_args()

    print("=" * 60)
    print("沪深主板个股多维度评级")
    print("=" * 60)

    fetcher = DataFetcher()
    asof_dt = datetime.now()
    asof = asof_dt.strftime("%Y%m%d")
    start = (asof_dt - timedelta(days=args.lookback + 40)).strftime("%Y%m%d")
    print(f"\n截面日: {asof}    回看起点: {start}")

    # 1. 全市场 universe（评级永远基于全市场分布）
    print("\n[1/4] 筛选沪深主板股票池...")
    universe = get_universe(fetcher, exclude_st=True, min_list_days=0)
    print(f"  股票池: {len(universe)} 只")

    # 2. spot + 资金流
    print("\n[2/4] 获取截面快照 + 主力资金流...")
    spot = fetcher.get_market_snapshot()
    print(f"  spot: {len(spot)} 只")
    fund_flow = fetcher.get_fund_flow_snapshot(window="5日排行")
    if fund_flow.empty:
        print("  fund_flow: 不可用（资金维度评级会缺失）")
    else:
        print(f"  fund_flow: {len(fund_flow)} 只")

    # 3. 历史日线
    print(f"\n[3/4] 拉取 {len(universe)} 只股票近 {args.lookback}+ 天日线...")
    panel = fetch_history_panel(fetcher, universe["ts_code"].tolist(), start, asof)
    print(f"  面板: {len(panel)} 行 × {panel['ts_code'].nunique()} 只")

    spot_cols = [c for c in ["ts_code", "pe_ttm", "pb", "total_mv", "circ_mv"] if c in spot.columns]
    panel = panel.merge(spot[spot_cols], on="ts_code", how="left")
    if not fund_flow.empty:
        flow_cols = [c for c in ["ts_code", "fund_inflow", "fund_outflow", "fund_net"]
                     if c in fund_flow.columns]
        panel = panel.merge(fund_flow[flow_cols], on="ts_code", how="left")

    # 4. 因子 + 评级
    print("\n[4/4] 计算因子 + 多维度评级...")
    factors = compute_all_factors(panel, asof_date=asof)
    print(f"  因子表: {factors.shape}")
    ratings = grade_all(factors)
    print(f"  评级: 3 维度 + 综合，共 {len(ratings)} 只参与分档")

    # 5. 合并股票名
    name_map = universe.set_index("ts_code")[["name"]]
    out = ratings.join(name_map, how="left")

    # 6. 筛输出范围
    if args.codes:
        wanted = [_normalize_code(c) for c in args.codes]
        missing = [c for c in wanted if c not in out.index]
        if missing:
            print(f"\n  [warn] 以下代码不在主板股票池中: {missing}")
        out = out.loc[[c for c in wanted if c in out.index]]
    elif args.top > 0:
        out = out.sort_values("score_total", ascending=False).head(args.top)
    elif args.all:
        out = out.sort_values("score_total", ascending=False)
    else:
        # 默认行为：输出 Top 50
        out = out.sort_values("score_total", ascending=False).head(50)

    # 7. 输出
    display_cols = [
        "name", "grade_total", "score_total",
        "grade_value", "score_value",
        "grade_tech",  "score_tech",
        "grade_flow",  "score_flow",
    ]
    out = out[[c for c in display_cols if c in out.columns]]

    os.makedirs("output", exist_ok=True)
    out_path = os.path.join("output", f"rating_{asof}.csv")
    out.to_csv(out_path, encoding="utf-8-sig", float_format="%.4f")
    print(f"\n评级结果已保存: {out_path}")

    print("\n" + "=" * 60)
    print(f"评级展示 ({len(out)} 只)")
    print("=" * 60)
    pd.set_option("display.unicode.east_asian_width", True)
    pd.set_option("display.width", 220)
    show = out.copy()
    for c in ["score_total", "score_value", "score_tech", "score_flow"]:
        if c in show.columns:
            show[c] = show[c].round(3)
    print(show.to_string())


if __name__ == "__main__":
    main()
