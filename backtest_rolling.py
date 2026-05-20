"""动态滚动调权重回测

每个调仓日 t：
  1. 用过去 12 期 IC 平均值（不含 t 时刻），按规则调整 swing 默认权重
  2. 用调好的权重选 Top N
  3. 模拟 [t, t+1] 收益
  4. 计算 t 时刻 IC（用 [t, t+1] 实际收益 vs t 因子值），加入历史 buffer

关键：调权重只用 t 之前已知的 IC，避免未来函数。

用法:
    python backtest_rolling.py --start-year 2022 --end-year 2024 --limit 500
"""
import sys
import io
import os
import argparse
from collections import deque
from datetime import datetime, timedelta

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import numpy as np
import pandas as pd

from data.fetcher import DataFetcher
from universe import get_universe
from factors import compute_all_factors
from selector import score, top_n
from strategies import get_factor_weights
from backtest_simple import (
    TECH_ONLY_FACTORS, COMMISSION_TWO_WAY,
    _filter_tech_weights, get_rebal_dates,
    fetch_all_history, calc_metrics, fetch_benchmark,
)
from walk_forward import adjust_weights_by_ic


def run_rolling_backtest(panel_all, rebal_dates, base_weights,
                        top_n_val=50, lookback=60, stoploss=-0.08,
                        ic_window=12, ic_min_obs=6):
    """动态滚动调权重回测

    Args:
        ic_window: 用过去多少期 IC 调权重
        ic_min_obs: 调权重最少需要多少期历史 IC（之前用默认权重）

    Returns: (metrics, equity, period_returns, ic_history, weight_log)
    """
    factor_keys = list(base_weights.keys())
    ic_buffer = deque(maxlen=ic_window)   # 滚动 IC 缓冲
    period_returns = []
    equity = [1.0]
    weight_log = []   # 记录每期实际用的权重，用于诊断

    for i in range(len(rebal_dates) - 1):
        t = rebal_dates[i]
        t_next = rebal_dates[i + 1]
        t_dt = pd.to_datetime(t)
        cutoff_start = t_dt - timedelta(days=lookback + 30)
        window = panel_all[(panel_all["trade_date"] >= cutoff_start) &
                          (panel_all["trade_date"] <= t_dt)]
        if window["ts_code"].nunique() < 30:
            continue

        try:
            factors = compute_all_factors(window, asof_date=t)
        except Exception:
            continue

        # ---- 关键步骤 1: 用历史 IC 调权重 ----
        if len(ic_buffer) >= ic_min_obs:
            ic_df = pd.DataFrame(list(ic_buffer))
            ic_mean = ic_df.mean()
            weights = adjust_weights_by_ic(base_weights, ic_mean, threshold=0.02)
        else:
            weights = dict(base_weights)
        weight_log.append({"date": t, "weights": dict(weights),
                          "n_history": len(ic_buffer)})

        # ---- 步骤 2: 用 weights 选股 ----
        scored = score(factors, weights=weights, min_valid_factors=3)
        picks = top_n(scored, n=top_n_val)
        if picks.empty:
            continue
        picked_codes = picks.index.tolist()

        # ---- 步骤 3: 算 [t, t+1] 收益（含止损）----
        rets = []
        for tc in picked_codes:
            sw = panel_all[(panel_all["ts_code"] == tc) &
                          (panel_all["trade_date"] >= t_dt) &
                          (panel_all["trade_date"] <= pd.to_datetime(t_next))]
            if len(sw) < 2:
                continue
            close_start = sw.iloc[0]["close"]
            if pd.isna(close_start) or close_start <= 0:
                continue
            if stoploss < 0:
                sw_ret = sw["close"] / close_start - 1
                if (sw_ret <= stoploss).any():
                    ret = stoploss - 0.002
                else:
                    ret = sw.iloc[-1]["close"] / close_start - 1
            else:
                ret = sw.iloc[-1]["close"] / close_start - 1
            if pd.isna(ret):
                continue
            rets.append(ret)
        if not rets:
            continue
        port_ret = float(np.mean(rets)) - COMMISSION_TWO_WAY
        period_returns.append(port_ret)
        equity.append(equity[-1] * (1 + port_ret))

        # ---- 步骤 4: 算本期 IC（用全市场实际收益，更新 buffer）----
        all_codes = factors.index.tolist()
        next_ret = {}
        for tc in all_codes:
            sw = panel_all[(panel_all["ts_code"] == tc) &
                          (panel_all["trade_date"] >= t_dt) &
                          (panel_all["trade_date"] <= pd.to_datetime(t_next))]
            if len(sw) >= 2 and sw.iloc[0]["close"] > 0:
                r = sw.iloc[-1]["close"] / sw.iloc[0]["close"] - 1
                if not pd.isna(r):
                    next_ret[tc] = r
        if len(next_ret) >= 30:
            ret_series = pd.Series(next_ret)
            ic_row = {}
            for fk in factor_keys:
                if fk in factors.columns:
                    f_series = factors[fk].dropna()
                    common = f_series.index.intersection(ret_series.index)
                    if len(common) >= 30:
                        f_rank = f_series.loc[common].rank()
                        r_rank = ret_series.loc[common].rank()
                        ic_row[fk] = float(f_rank.corr(r_rank, method="pearson"))
            ic_buffer.append(ic_row)

    metrics = calc_metrics(pd.Series(equity), pd.Series(period_returns), interval_weeks=2)
    return metrics, equity, period_returns, list(ic_buffer), weight_log


def main():
    parser = argparse.ArgumentParser(description="动态滚动调权重回测")
    parser.add_argument("--start-year", type=int, default=2022)
    parser.add_argument("--end-year",   type=int, default=2024)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--top",   type=int, default=50)
    parser.add_argument("--lookback", type=int, default=60)
    parser.add_argument("--rebal-weeks", type=int, default=2)
    parser.add_argument("--ic-window", type=int, default=12,
                        help="滚动 IC 窗口（期数，1 期=rebal_weeks 周）")
    parser.add_argument("--ic-min-obs", type=int, default=6,
                        help="调权重最少历史 IC 期数，之前用默认权重")
    parser.add_argument("--strategy", default="swing")
    parser.add_argument("--stoploss", type=float, default=-0.08)
    args = parser.parse_args()

    print("=" * 70)
    print(f"动态滚动调权重回测: {args.start_year}~{args.end_year}, {args.limit} 只主板")
    print(f"基准策略: {args.strategy}, IC 窗口: {args.ic_window} 期 ({args.ic_window*args.rebal_weeks} 周)")
    print("=" * 70)

    fetcher = DataFetcher()
    fetch_start_dt = datetime(args.start_year, 1, 1) - timedelta(days=args.lookback + 60)
    fetch_end_dt = datetime(args.end_year, 12, 31)
    fetch_start = fetch_start_dt.strftime("%Y%m%d")
    fetch_end = fetch_end_dt.strftime("%Y%m%d")
    print(f"\n数据区间: {fetch_start} ~ {fetch_end}")

    universe = get_universe(fetcher, exclude_st=True, min_list_days=0)
    universe = universe.head(args.limit)
    ts_codes = universe["ts_code"].tolist()
    print(f"股票池: {len(ts_codes)} 只")

    print(f"\n拉取历史日线...")
    panel_all = fetch_all_history(fetcher, ts_codes, fetch_start, fetch_end)
    panel_all["trade_date"] = pd.to_datetime(panel_all["trade_date"])
    print(f"  面板: {len(panel_all)} 行")

    trade_dates = fetcher.get_trade_dates(fetch_start, fetch_end)
    start_dt = datetime(args.start_year, 1, 1)
    end_dt = datetime(args.end_year, 12, 31)
    rebal_dates = get_rebal_dates(start_dt, end_dt, trade_dates,
                                  interval_weeks=args.rebal_weeks)
    print(f"调仓次数: {len(rebal_dates)}")

    base_weights = _filter_tech_weights(get_factor_weights(args.strategy))

    # ---- 1. 静态基线（不调权重）----
    print(f"\n[1] 静态基线（不调权重）...")
    from walk_forward import run_period_backtest
    static_m, _, _, _ = run_period_backtest(
        panel_all, rebal_dates, base_weights,
        top_n_val=args.top, lookback=args.lookback, stoploss=args.stoploss)
    print(f"  静态: 年化 {static_m['ann_return']*100:+.2f}%, "
          f"夏普 {static_m['sharpe']:.2f}, 回撤 {static_m['max_drawdown']*100:+.2f}%")

    # ---- 2. 动态滚动调权重 ----
    print(f"\n[2] 动态滚动调权重（IC 窗口 {args.ic_window} 期，启动期 {args.ic_min_obs} 期）...")
    rolling_m, equity, _, _, weight_log = run_rolling_backtest(
        panel_all, rebal_dates, base_weights,
        top_n_val=args.top, lookback=args.lookback, stoploss=args.stoploss,
        ic_window=args.ic_window, ic_min_obs=args.ic_min_obs)
    print(f"  动态: 年化 {rolling_m['ann_return']*100:+.2f}%, "
          f"夏普 {rolling_m['sharpe']:.2f}, 回撤 {rolling_m['max_drawdown']*100:+.2f}%")

    # ---- 3. 基准 ----
    bm = fetch_benchmark(fetcher, rebal_dates[0], rebal_dates[-1])
    bm_ret = None
    if not bm.empty:
        keys = sorted(bm.index)
        if len(keys) >= 2:
            bm_ret = float(bm[keys[-1]] / bm[keys[0]] - 1)

    # ---- 4. 对比 ----
    delta_ann = rolling_m["ann_return"] - static_m["ann_return"]
    print("\n" + "=" * 70)
    print(f"对比 ({args.start_year}~{args.end_year}, {static_m['n_periods']} 期)")
    print("=" * 70)
    print(f"  静态权重:    年化 {static_m['ann_return']*100:>+7.2f}%, "
          f"夏普 {static_m['sharpe']:.2f}, 回撤 {static_m['max_drawdown']*100:>+6.2f}%, "
          f"胜率 {static_m['win_rate']*100:>5.1f}%")
    print(f"  动态调权重:  年化 {rolling_m['ann_return']*100:>+7.2f}%, "
          f"夏普 {rolling_m['sharpe']:.2f}, 回撤 {rolling_m['max_drawdown']*100:>+6.2f}%, "
          f"胜率 {rolling_m['win_rate']*100:>5.1f}%")
    if bm_ret is not None:
        # 沪深300 年化
        years = static_m["total_weeks"] / 52
        bm_ann = (1 + bm_ret) ** (1 / max(years, 0.5)) - 1
        print(f"  沪深300:    年化 {bm_ann*100:>+7.2f}% (总收益 {bm_ret*100:+.2f}%)")

    print()
    if delta_ann > 0.02:
        print(f"  [结论] 动态调权重有效 (+{delta_ann*100:.2f}% 年化)")
    elif delta_ann < -0.02:
        print(f"  [结论] 动态调权重恶化 ({delta_ann*100:+.2f}% 年化)")
    else:
        print(f"  [结论] 动态调权重无显著差异 ({delta_ann*100:+.2f}% 年化)")

    # 输出权重切换历史
    n_default = sum(1 for w in weight_log if w["n_history"] < args.ic_min_obs)
    n_dynamic = len(weight_log) - n_default
    print(f"\n  启动期（用默认权重）: {n_default} 期")
    print(f"  动态期（用历史 IC 调）: {n_dynamic} 期")


if __name__ == "__main__":
    main()
