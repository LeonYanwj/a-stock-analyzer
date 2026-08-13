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
from financial_risk import attach_financial_risk
from rating_store import prepare_snapshot, save_snapshot
from strategies import (get_factor_weights, list_strategies,
                        calc_optimal_top_n, warn_if_capital_too_small,
                        position_range, validate_top_n)


LOOKBACK_DAYS = 60
TOP_N = 50
API_SLEEP = 0.05   # AKShare 无硬限流，留极小间隔避免触发风控
_BASE_MARKET_CACHE = {}
_BASE_CACHE_TTL_SECONDS = 15 * 60


def fetch_history_panel(fetcher: DataFetcher, ts_codes, start, end) -> pd.DataFrame:
    """批量查库，只有缺失或行情未更新的股票才逐只调用外部接口。"""
    frames, fail = [], 0
    ts_codes = list(dict.fromkeys(ts_codes))
    expected_dates = fetcher.get_trade_dates(start, end)
    expected_last = pd.to_datetime(expected_dates[-1]) if expected_dates else None
    start_sql = pd.to_datetime(start).date()
    end_sql = pd.to_datetime(end).date()

    db_panel = pd.DataFrame()
    try:
        from data.db import get_conn
        chunks = []
        with get_conn() as conn:
            for offset in range(0, len(ts_codes), 500):
                batch = ts_codes[offset:offset + 500]
                placeholders = ",".join(["%s"] * len(batch))
                sql = (
                    "SELECT * FROM market_daily WHERE adjust='qfq' "
                    "AND trade_date BETWEEN %s AND %s "
                    f"AND ts_code IN ({placeholders})")
                chunks.append(pd.read_sql(sql, conn, params=[start_sql, end_sql] + batch))
        chunks = [chunk for chunk in chunks if not chunk.empty]
        if chunks:
            db_panel = pd.concat(chunks, ignore_index=True)
            db_panel["trade_date"] = pd.to_datetime(db_panel["trade_date"])
    except Exception as e:
        print(f"  [warn] 批量行情查库失败，降级逐股获取: {type(e).__name__}: {e}")

    usable = set()
    if not db_panel.empty:
        coverage = db_panel.groupby("ts_code")["trade_date"].agg(["count", "max"])
        for tc, row in coverage.iterrows():
            latest_ok = expected_last is None or row["max"] >= expected_last
            if row["count"] >= 31 and latest_ok:
                usable.add(tc)
        if usable:
            frames.append(db_panel[db_panel["ts_code"].isin(usable)])
    missing = [tc for tc in ts_codes if tc not in usable]
    if usable:
        print(f"  [db] 行情完整 {len(usable)} 只，需增量获取 {len(missing)} 只")

    total = len(missing)
    t0 = time.time()
    for i, ts_code in enumerate(missing, 1):
        df = fetcher.get_daily(ts_code, start, end)
        if df is None or df.empty:
            fail += 1
        else:
            frames.append(df)
        if total and (i % 100 == 0 or i == total):
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (total - i) / rate if rate > 0 else 0
            print(f"  [{i}/{total}]  耗时 {elapsed:.0f}s  ETA {eta:.0f}s  失败 {fail}",
                  flush=True)
        if API_SLEEP > 0:
            time.sleep(API_SLEEP)
    if not frames:
        raise RuntimeError("没有拉到任何历史数据")
    panel = pd.concat(frames, ignore_index=True)
    return panel.drop_duplicates(["ts_code", "trade_date"], keep="last")


def build_market_factors(asof_dt, lookback: int, limit: int = 0,
                         verbose: bool = True):
    """拉取一次全市场基础数据并计算策略无关的原始因子。"""
    asof = asof_dt.strftime("%Y%m%d")
    cache_key = (asof, int(lookback), int(limit))
    cached = _BASE_MARKET_CACHE.get(cache_key)
    if cached and time.time() - cached["cached_at"] <= _BASE_CACHE_TTL_SECONDS:
        if verbose:
            print("  复用本次运行的全市场基础因子缓存")
        return (cached["fetcher"], cached["universe"].copy(),
                cached["factors"].copy(), cached["effective_date"])

    fetcher = DataFetcher()
    start = (asof_dt - timedelta(days=lookback + 30)).strftime("%Y%m%d")
    universe = get_universe(
        fetcher, exclude_st=True, min_list_days=365, asof_date=asof_dt)
    fin_df = fetcher.get_financial_latest_all()
    universe = attach_financial_risk(universe, fin_df)
    excluded_financial = int((~universe["eligible"]).sum())
    universe = universe[universe["eligible"]].copy()
    if limit > 0:
        universe = universe.head(limit)
    if universe.empty:
        raise RuntimeError("股票池为空，请检查 market_stock_basic 的上市日期和 ST 数据")
    if verbose:
        print(f"  股票池: {len(universe)} 只（财务高风险剔除 {excluded_financial} 只）")

    spot = fetcher.get_market_snapshot()
    fund_flow = fetcher.get_fund_flow_snapshot(window="5日排行")
    lxsz_df = fetcher.get_stock_rank_lxsz()
    if verbose and not fin_df.empty:
        print(f"  financial: {len(fin_df)} 只覆盖")
    if verbose:
        print(f"  拉取 {len(universe)} 只股票 {lookback}+ 天日线...")
    panel = fetch_history_panel(fetcher, universe["ts_code"].tolist(), start, asof)

    spot_cols = [c for c in ["ts_code", "pe_ttm", "pb", "total_mv", "circ_mv"]
                 if c in spot.columns]
    panel = panel.merge(spot[spot_cols], on="ts_code", how="left")
    if not fund_flow.empty:
        flow_cols = [c for c in ["ts_code", "fund_inflow", "fund_outflow", "fund_net"]
                     if c in fund_flow.columns]
        panel = panel.merge(fund_flow[flow_cols], on="ts_code", how="left")
    if not lxsz_df.empty and "lxsz_days" in lxsz_df.columns:
        panel = panel.merge(lxsz_df[["ts_code", "lxsz_days"]], on="ts_code", how="left")
    if not fin_df.empty:
        fin_cols = [c for c in ["ts_code", "roe", "gross_margin", "net_margin",
                                "debt_ratio", "net_profit_yoy", "revenue_yoy"]
                    if c in fin_df.columns]
        panel = panel.merge(fin_df[fin_cols], on="ts_code", how="left")

    factors = compute_all_factors(panel, asof_date=asof)
    # 外部行情完全缺失的股票也保留一条 N/A 评级，便于追踪数据覆盖问题。
    factors = factors.reindex(universe["ts_code"].tolist())
    effective_date = pd.to_datetime(panel["trade_date"]).max().date()
    _BASE_MARKET_CACHE.clear()
    _BASE_MARKET_CACHE[cache_key] = {
        "cached_at": time.time(), "fetcher": fetcher,
        "universe": universe.copy(), "factors": factors.copy(),
        "effective_date": effective_date,
    }
    return fetcher, universe, factors, effective_date


def screen_market(strategy: str = "swing", capital: float = 0,
                  top_n_arg: int = 0, lookback: int = LOOKBACK_DAYS,
                  limit: int = 0, enable_news: bool = False,
                  refine: int = 100, news_weight: float = 0.15,
                  verbose: bool = True, return_all: bool = False,
                  persist_ratings: bool = True, as_of_dt=None) -> pd.DataFrame:
    """全市场选股核心函数（可被其他脚本调用，如 paper.py）

    Returns:
        DataFrame: index=ts_code, 列含 name/score/各因子值
        空 DataFrame 表示选股失败
    """
    # 确定 top_n
    if top_n_arg > 0:
        top_actual = top_n_arg
    elif capital > 0:
        top_actual = calc_optimal_top_n(capital)
    else:
        top_actual = TOP_N

    # 计划、回放和回测必须传入决策截面，禁止在调用链中悄悄改成当前时间。
    asof_dt = as_of_dt or datetime.now()
    asof = asof_dt.strftime("%Y%m%d")

    if verbose:
        print(f"[screen] strategy={strategy}, top={top_actual}, 截面日={asof}")

    fetcher, universe, factors, effective_date = build_market_factors(
        asof_dt, lookback=lookback, limit=limit, verbose=verbose)
    weights = get_factor_weights(strategy)
    scored = score(factors, weights=weights)
    risk_cols = ["ts_code", "financial_risk_level", "financial_risk_flags"]
    risk_meta = universe[[c for c in risk_cols if c in universe.columns]].set_index("ts_code")
    scored = scored.join(risk_meta, how="left")

    # 4.5 消息面二次精筛（可选）
    if enable_news:
        valid_top = scored.dropna(subset=["score"]).head(refine)
        if verbose:
            print(f"  对 Top {len(valid_top)} 拉新闻精筛...")
        news_scores = {}
        for tc in valid_top.index:
            ns = compute_news_score(
                fetcher.get_stock_news(tc),
                fetcher.get_stock_disclosure(tc, days=30),
                fetcher.get_stock_research(tc))
            if not pd.isna(ns["news_score"]):
                news_scores[tc] = ns["news_score"]
        scored["news_score"] = pd.Series(news_scores)
        scored["news_bonus"] = scored["news_score"].fillna(0) * news_weight
        scored["final_score"] = scored["score"] + scored["news_bonus"]
        in_refine = scored.index.isin(valid_top.index)
        scored.loc[in_refine, "score"] = scored.loc[in_refine, "final_score"]
        scored = scored.sort_values("score", ascending=False)

    name_map = universe.set_index("ts_code")[["name"]]
    scored = scored.join(name_map, how="left")

    snapshot = prepare_snapshot(scored, effective_date, strategy)
    if persist_ratings and not snapshot.empty:
        try:
            saved = save_snapshot(snapshot)
            if verbose:
                print(f"  每日评级快照: {saved} 只")
        except Exception as e:
            if verbose:
                print(f"  [warn] 评级快照保存失败: {type(e).__name__}: {e}")

    if return_all:
        return snapshot
    return top_n(snapshot, n=top_actual)


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

    out = screen_market(
        strategy=args.strategy,
        capital=args.capital,
        top_n_arg=args.top,
        lookback=args.lookback,
        limit=args.limit,
        enable_news=args.news,
        refine=args.refine,
        news_weight=args.news_weight,
        verbose=True,
    )
    asof = (pd.to_datetime(out["trade_date"].iloc[0]).strftime("%Y%m%d")
            if not out.empty and "trade_date" in out.columns
            else datetime.now().strftime("%Y%m%d"))
    cols = ["name", "score", "grade", "rank_num", "trend_state",
            "financial_risk_level", "valid_factors", "weight_coverage",
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
