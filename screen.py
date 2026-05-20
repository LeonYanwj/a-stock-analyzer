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
from news_scorer import compute_news_score
from strategies import (get_factor_weights, list_strategies,
                        calc_optimal_top_n, warn_if_capital_too_small,
                        position_range, validate_top_n)


LOOKBACK_DAYS = 60
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
    parser.add_argument("--top", type=int, default=0,
                        help="选股 Top N（默认 0=自动按 --capital 算；都不传则用 50）")
    parser.add_argument("--capital", type=float, default=0,
                        help="资金量（元），自动映射持仓数。如 --capital 100000 → 持仓 10 只")
    parser.add_argument("--lookback", type=int, default=LOOKBACK_DAYS, help="历史回看天数")
    parser.add_argument("--strategy", default="swing", choices=list_strategies(),
                        help="交易策略 profile: short_term/swing/trend (默认 swing)")
    parser.add_argument("--news", action="store_true",
                        help="开启消息面二次精筛（对 Top --refine 候选拉新闻+公告+研报）")
    parser.add_argument("--refine", type=int, default=100,
                        help="消息面精筛候选数（默认 Top 100）")
    parser.add_argument("--news-weight", type=float, default=0.15,
                        help="news_score 加分系数（每 1 分 ~ 加 0.15 到总分）")
    args = parser.parse_args()

    # 持仓数逻辑：
    # 1. 传 --top: 用用户值；若也传了 --capital，检查是否在合理区间
    # 2. 只传 --capital: 用推荐值（公式算）
    # 3. 都不传: 用默认 50
    if args.top > 0:
        top_actual = args.top
        if args.capital > 0:
            warn = validate_top_n(top_actual, args.capital)
            if warn:
                print(warn)
    elif args.capital > 0:
        mn, mx = position_range(args.capital)
        top_actual = calc_optimal_top_n(args.capital)
        warn = warn_if_capital_too_small(args.capital)
        if warn:
            print(warn)
        print(f"[资金 {args.capital:,.0f} → 推荐持仓 {top_actual} 只 "
              f"(允许区间 [{mn}, {mx}])  单股 ~{args.capital/top_actual:,.0f} 元]")
    else:
        top_actual = TOP_N
    args.top = top_actual

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

    # 2. 全市场截面快照（PE/PB/市值/换手率）+ 资金流 + 量价齐升榜
    print("\n[2/4] 获取全市场截面快照 + 资金流 + 量价齐升...")
    spot = fetcher.get_market_snapshot()
    print(f"  spot: {len(spot)} 只")
    fund_flow = fetcher.get_fund_flow_snapshot(window="5日排行")
    print(f"  fund_flow: {len(fund_flow)} 只" if not fund_flow.empty else "  fund_flow: 不可用（资金流因子会跳过）")
    lxsz_df = fetcher.get_stock_rank_lxsz()

    # 3. 历史日线（用于动量/反转/波动率因子）
    print(f"\n[3/4] 拉取 {len(universe)} 只股票近 {args.lookback}+ 天日线...")
    panel = fetch_history_panel(fetcher, universe["ts_code"].tolist(), start, asof)
    print(f"  面板: {len(panel)} 行 × {panel['ts_code'].nunique()} 只")

    # 把 spot 截面字段 merge 到 panel（compute 时 snap 取最新日截面）
    spot_cols = [c for c in ["ts_code", "pe_ttm", "pb", "total_mv", "circ_mv"] if c in spot.columns]
    panel = panel.merge(spot[spot_cols], on="ts_code", how="left")

    # 把资金流字段也 merge 进 panel
    if not fund_flow.empty:
        flow_cols = [c for c in ["ts_code", "fund_inflow", "fund_outflow", "fund_net"]
                     if c in fund_flow.columns]
        panel = panel.merge(fund_flow[flow_cols], on="ts_code", how="left")

    # 把量价齐升 lxsz_days merge 进 panel（榜外股票自动是 NaN，后续 fillna(0)）
    if not lxsz_df.empty and "lxsz_days" in lxsz_df.columns:
        panel = panel.merge(lxsz_df[["ts_code", "lxsz_days"]], on="ts_code", how="left")

    # 4. 因子 + 打分（按策略选权重）
    print(f"\n[4/4] 计算因子 + 打分排序... (strategy={args.strategy})")
    factors = compute_all_factors(panel, asof_date=asof)
    print(f"  因子表: {factors.shape[0]} 只 × {factors.shape[1]} 因子")
    weights = get_factor_weights(args.strategy)
    scored = score(factors, weights=weights)

    # 4.5  消息面二次精筛（可选，--news 开启）
    if args.news:
        valid_top = scored.dropna(subset=["score"]).head(args.refine)
        print(f"\n[5/5] 对 Top {len(valid_top)} 候选拉取消息面（新闻+公告+研报）...")
        news_scores = {}
        for i, tc in enumerate(valid_top.index, 1):
            news_df = fetcher.get_stock_news(tc)
            disc_df = fetcher.get_stock_disclosure(tc, days=30)
            rsr_df = fetcher.get_stock_research(tc)
            ns = compute_news_score(news_df, disc_df, rsr_df)
            if not pd.isna(ns["news_score"]):
                news_scores[tc] = ns["news_score"]
            if i % 20 == 0 or i == len(valid_top):
                print(f"  [{i}/{len(valid_top)}]  累计有新闻数据: {len(news_scores)}")
        # 将 news_score 加到原 score 形成 final_score
        scored["news_score"] = pd.Series(news_scores)
        scored["news_bonus"] = scored["news_score"].fillna(0) * args.news_weight
        scored["final_score"] = scored["score"] + scored["news_bonus"]
        # 仅对 Top refine 集内的股票使用 final_score 重排；其他保持原序
        in_refine = scored.index.isin(valid_top.index)
        scored.loc[in_refine, "score"] = scored.loc[in_refine, "final_score"]
        scored = scored.sort_values("score", ascending=False)
        print(f"  消息面已加权（系数 {args.news_weight}），重排序完成")

    picks = top_n(scored, n=args.top)

    # 5. 输出
    name_map = universe.set_index("ts_code")[["name"]]
    out = picks.join(name_map, how="left")
    cols = ["name", "score", "valid_factors",
            "ep_ttm", "bp", "mom_30", "reversal_5", "small_size", "low_vol", "liquidity",
            "main_inflow", "inflow_ratio", "macd_hist", "macd_slope", "lxsz",
            "pattern_score", "news_score", "news_bonus"]
    out = out[[c for c in cols if c in out.columns]]

    os.makedirs("output", exist_ok=True)
    out_path = os.path.join("output", f"picks_{args.strategy}_{asof}.csv")
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
