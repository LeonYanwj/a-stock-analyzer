"""多窗口 Walk-Forward 验证（判断 alpha 是否稳定）

用 N 个不重叠的时间窗口分别跑 walk-forward，看：
- 测试集 (旧权重) vs 沪深300 → 因子策略本身是否能 alpha
- 测试集 (IC 调权重) vs 测试集 (旧权重) → IC 调权重是否真有效

只有当 IC 调权重在 ≥ 60% 的窗口里跑赢旧权重，且平均 alpha > 0，
才能认为方法稳定。否则前面的"+5.3% alpha"就是单次运气。

用法:
    python multi_window.py --start-year 2022 --end-year 2024
"""
import sys
import io
import os
import argparse
from datetime import datetime, timedelta

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import numpy as np
import pandas as pd

from data.fetcher import DataFetcher
from universe import get_universe
from strategies import get_factor_weights
from backtest_simple import (
    _filter_tech_weights, get_rebal_dates,
    fetch_all_history, fetch_benchmark,
)
from walk_forward import run_period_backtest, adjust_weights_by_ic


def run_one_window(panel_all, trade_dates,
                   train_start, train_end, test_start, test_end,
                   weights, top_n_val, lookback, rebal_weeks, stoploss):
    """对一个窗口跑 walk-forward，返回测试集 (旧 vs 新) 绩效 + IC"""
    train_rebal = get_rebal_dates(train_start, train_end, trade_dates,
                                  interval_weeks=rebal_weeks)
    test_rebal = get_rebal_dates(test_start, test_end, trade_dates,
                                 interval_weeks=rebal_weeks)
    if len(train_rebal) < 4 or len(test_rebal) < 4:
        return None, None, None, None

    # 训练集（旧权重）+ 收集 IC
    train_m, _, _, ic_df = run_period_backtest(
        panel_all, train_rebal, weights,
        top_n_val=top_n_val, lookback=lookback,
        stoploss=stoploss, collect_ic=True)
    if ic_df.empty:
        return train_m, None, None, None
    ic_mean = ic_df.mean()
    new_weights = adjust_weights_by_ic(weights, ic_mean, threshold=0.02)

    # 测试集（旧 + 新）
    test_old_m, _, _, _ = run_period_backtest(
        panel_all, test_rebal, weights,
        top_n_val=top_n_val, lookback=lookback, stoploss=stoploss)
    test_new_m, _, _, _ = run_period_backtest(
        panel_all, test_rebal, new_weights,
        top_n_val=top_n_val, lookback=lookback, stoploss=stoploss)

    return train_m, test_old_m, test_new_m, ic_mean


def main():
    parser = argparse.ArgumentParser(description="多窗口 walk-forward 验证")
    parser.add_argument("--start-year", type=int, default=2022)
    parser.add_argument("--end-year",   type=int, default=2024)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--top",   type=int, default=30)
    parser.add_argument("--lookback", type=int, default=60)
    parser.add_argument("--rebal-weeks", type=int, default=2)
    parser.add_argument("--strategy", default="swing")
    parser.add_argument("--stoploss", type=float, default=-0.08)
    args = parser.parse_args()

    print("=" * 70)
    print(f"多窗口 Walk-Forward 验证: {args.start_year}~{args.end_year}, "
          f"{args.limit} 只主板")
    print("=" * 70)

    fetcher = DataFetcher()

    # 数据时间：拉够每个窗口的训练+测试+lookback buffer
    fetch_start_dt = datetime(args.start_year, 1, 1) - timedelta(days=args.lookback + 60)
    fetch_end_dt = datetime(args.end_year, 12, 31)
    fetch_start = fetch_start_dt.strftime("%Y%m%d")
    fetch_end = fetch_end_dt.strftime("%Y%m%d")
    print(f"\n数据区间: {fetch_start} ~ {fetch_end}")

    # 股票池
    universe = get_universe(fetcher, exclude_st=True, min_list_days=0)
    universe = universe.head(args.limit)
    ts_codes = universe["ts_code"].tolist()
    print(f"股票池: {len(ts_codes)} 只")

    # 一次拉所有数据
    print(f"\n拉取历史日线...")
    panel_all = fetch_all_history(fetcher, ts_codes, fetch_start, fetch_end)
    panel_all["trade_date"] = pd.to_datetime(panel_all["trade_date"])
    print(f"  面板: {len(panel_all)} 行")

    trade_dates = fetcher.get_trade_dates(fetch_start, fetch_end)
    old_weights = _filter_tech_weights(get_factor_weights(args.strategy))

    # 定义不重叠的年度窗口（训练上半年 + 测试下半年）
    windows = []
    for yr in range(args.start_year, args.end_year + 1):
        train_start = datetime(yr, 1, 1)
        train_end   = datetime(yr, 7, 1)
        test_start  = datetime(yr, 7, 1)
        test_end    = datetime(yr, 12, 31)
        windows.append((yr, train_start, train_end, test_start, test_end))

    # 逐个窗口跑
    print(f"\n开始 {len(windows)} 个窗口...\n")
    results = []
    for (yr, ts, te, vs, ve) in windows:
        print(f"--- 窗口 {yr} (训练 {ts.date()}~{te.date()}, "
              f"测试 {vs.date()}~{ve.date()}) ---")
        train_m, test_old, test_new, ic_mean = run_one_window(
            panel_all, trade_dates, ts, te, vs, ve,
            old_weights, args.top, args.lookback,
            args.rebal_weeks, args.stoploss)
        if test_old is None or test_new is None:
            print(f"  窗口跳过（数据不足）")
            continue

        # 基准
        bm = fetch_benchmark(fetcher, vs.strftime("%Y%m%d"), ve.strftime("%Y%m%d"))
        bm_ret = None
        if not bm.empty:
            keys = sorted(bm.index)
            if len(keys) >= 2:
                bm_ret = float(bm[keys[-1]] / bm[keys[0]] - 1)

        results.append({
            "year": yr,
            "train_ann":    train_m["ann_return"],
            "test_old_ann": test_old["ann_return"],
            "test_new_ann": test_new["ann_return"],
            "test_old_sharpe": test_old["sharpe"],
            "test_new_sharpe": test_new["sharpe"],
            "test_old_dd":  test_old["max_drawdown"],
            "test_new_dd":  test_new["max_drawdown"],
            "bm_ret":       bm_ret,
            "ic_mean":      ic_mean,
        })
        print(f"  训练: {train_m['ann_return']*100:+.2f}%")
        print(f"  测试 (旧): {test_old['ann_return']*100:+.2f}% "
              f"(夏普 {test_old['sharpe']:.2f}, 回撤 {test_old['max_drawdown']*100:+.2f}%)")
        print(f"  测试 (新): {test_new['ann_return']*100:+.2f}% "
              f"(夏普 {test_new['sharpe']:.2f}, 回撤 {test_new['max_drawdown']*100:+.2f}%)")
        if bm_ret is not None:
            print(f"  沪深300: {bm_ret*100:+.2f}%")
        print()

    if not results:
        print("[error] 无任何有效窗口")
        return

    # ---- 汇总输出 ----
    df = pd.DataFrame(results)
    print("=" * 70)
    print("汇总")
    print("=" * 70)
    print(f"{'年份':<8} {'训练':>9} {'测试(旧)':>10} {'测试(新)':>10} "
          f"{'调权效益':>10} {'沪深300':>10} {'超额(旧)':>10} {'超额(新)':>10}")
    for r in results:
        delta = r["test_new_ann"] - r["test_old_ann"]
        bm = r["bm_ret"] if r["bm_ret"] is not None else 0
        excess_old = r["test_old_ann"] - bm
        excess_new = r["test_new_ann"] - bm
        bm_disp = f"{bm*100:+.2f}%" if r["bm_ret"] is not None else "  N/A"
        print(f"{r['year']:<8} "
              f"{r['train_ann']*100:>+8.2f}% "
              f"{r['test_old_ann']*100:>+9.2f}% "
              f"{r['test_new_ann']*100:>+9.2f}% "
              f"{delta*100:>+9.2f}% "
              f"{bm_disp:>10} "
              f"{excess_old*100:>+9.2f}% "
              f"{excess_new*100:>+9.2f}%")

    # 平均 + 胜率
    avg_old = df["test_old_ann"].mean()
    avg_new = df["test_new_ann"].mean()
    avg_delta = (df["test_new_ann"] - df["test_old_ann"]).mean()
    win_rate = ((df["test_new_ann"] > df["test_old_ann"]).sum() / len(df))

    print("-" * 70)
    print(f"  平均测试 (旧权重)年化: {avg_old*100:+.2f}%")
    print(f"  平均测试 (新权重)年化: {avg_new*100:+.2f}%")
    print(f"  平均调权效益:         {avg_delta*100:+.2f}%")
    print(f"  IC 调权胜率:          {win_rate*100:.0f}%  ({(df['test_new_ann']>df['test_old_ann']).sum()}/{len(df)} 窗口)")

    # 结论
    print()
    if avg_delta > 0.02 and win_rate >= 0.6:
        print(f"  [结论] IC 调权重在多窗口下**稳定有效** "
              f"(平均 +{avg_delta*100:.2f}%, 胜率 {win_rate*100:.0f}%)")
    elif avg_delta > 0 and win_rate >= 0.5:
        print(f"  [结论] IC 调权重**可能有效但不稳定** "
              f"(平均 +{avg_delta*100:.2f}%, 胜率 {win_rate*100:.0f}%)")
    else:
        print(f"  [结论] IC 调权重在多窗口下**无显著效果** "
              f"(平均 {avg_delta*100:+.2f}%, 胜率 {win_rate*100:.0f}%) "
              f"—— 之前单次的 alpha 是运气")

    # 保存详细结果
    os.makedirs("output", exist_ok=True)
    out = df.drop(columns=["ic_mean"]).copy()
    out_path = os.path.join("output",
                            f"multi_window_{args.start_year}_{args.end_year}.csv")
    out.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n详细结果: {out_path}")


if __name__ == "__main__":
    main()
