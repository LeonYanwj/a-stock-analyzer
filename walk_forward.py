"""Walk-Forward 样本外验证

判断"按 IC 调权重"是真有效还是样本内过拟合：

  时间线: [---训练 6 个月---][---测试 6 个月---]
                ↓
          算 IC + 调权重
                ↓
          用新权重在测试集回测

输出 3 条净值曲线对比：
  1. 训练集（旧权重）     —— 基线
  2. 测试集（旧权重）     —— 不调权重的对照
  3. 测试集（IC 调整后权重）—— 调权重的实验

如果 #3 > #2 → IC 调权重在样本外仍有效
如果 #3 ≤ #2 → 之前的"按 IC 调权重"就是过拟合
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
from factors import compute_all_factors
from selector import score, top_n
from strategies import get_factor_weights
from backtest_simple import (
    TECH_ONLY_FACTORS, COMMISSION_TWO_WAY,
    _filter_tech_weights, get_rebal_dates,
    fetch_all_history, calc_metrics, fetch_benchmark,
)


def run_period_backtest(panel_all: pd.DataFrame, rebal_dates: list,
                        weights: dict, top_n_val: int = 30,
                        lookback: int = 60, stoploss: float = -0.08,
                        collect_ic: bool = False, label: str = ""):
    """对给定时间区间 + 权重，跑滑窗回测

    Returns: (metrics, equity_list, period_returns_list, ic_df)
    """
    period_returns = []
    equity = [1.0]
    ic_records = []
    factor_keys = list(weights.keys())

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
            scored = score(factors, weights=weights, min_valid_factors=3)
            picks = top_n(scored, n=top_n_val)
        except Exception:
            continue
        if picks.empty:
            continue
        picked_codes = picks.index.tolist()
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

        if collect_ic:
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
                ic_row = {"date": t}
                for fk in factor_keys:
                    if fk in factors.columns:
                        f_series = factors[fk].dropna()
                        common = f_series.index.intersection(ret_series.index)
                        if len(common) >= 30:
                            f_rank = f_series.loc[common].rank()
                            r_rank = ret_series.loc[common].rank()
                            ic_row[fk] = float(f_rank.corr(r_rank, method="pearson"))
                ic_records.append(ic_row)

    ic_df = pd.DataFrame(ic_records).set_index("date") if ic_records else pd.DataFrame()
    metrics = calc_metrics(pd.Series(equity), pd.Series(period_returns), interval_weeks=2)
    return metrics, equity, period_returns, ic_df


def adjust_weights_by_ic(old_weights: dict, ic_mean: pd.Series,
                         threshold: float = 0.02) -> dict:
    """根据 IC 自动调权重（保守版，避免过拟合）

    规则：
      IC > +threshold   : 权重 ×1.5（强化）
      0 < IC ≤ threshold: 权重 ×1.0（保持）
      -threshold ≤ IC ≤ 0: 权重 ×0.5（减半）
      IC < -threshold   : 权重 = 0（关闭，不反向用）
    """
    new = dict(old_weights)
    for k in new:
        if k not in ic_mean.index or pd.isna(ic_mean[k]):
            continue
        ic = ic_mean[k]
        if ic > threshold:
            new[k] = new[k] * 1.5
        elif ic > 0:
            new[k] = new[k]
        elif ic > -threshold:
            new[k] = new[k] * 0.5
        else:
            new[k] = 0.0
    return new


def main():
    parser = argparse.ArgumentParser(description="Walk-Forward 样本外验证")
    parser.add_argument("--train-months", type=int, default=6)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--lookback", type=int, default=60)
    parser.add_argument("--rebal-weeks", type=int, default=2)
    parser.add_argument("--strategy", default="swing")
    parser.add_argument("--stoploss", type=float, default=-0.08)
    args = parser.parse_args()

    print("=" * 60)
    print(f"Walk-Forward 验证: 训练 {args.train_months} 月 + 测试 {args.test_months} 月")
    print("=" * 60)

    fetcher = DataFetcher()
    end_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    test_start = end_dt - timedelta(days=args.test_months * 31)
    train_start = test_start - timedelta(days=args.train_months * 31)
    fetch_start = (train_start - timedelta(days=args.lookback + 30)).strftime("%Y%m%d")
    fetch_end = end_dt.strftime("%Y%m%d")
    print(f"  训练: {train_start.date()} ~ {test_start.date()}")
    print(f"  测试: {test_start.date()} ~ {end_dt.date()}")

    # 1. 股票池
    universe = get_universe(fetcher, exclude_st=True, min_list_days=0)
    universe = universe.head(args.limit)
    ts_codes = universe["ts_code"].tolist()
    print(f"\n[1] 股票池: {len(ts_codes)} 只")

    # 2. 一次拉所有数据
    print(f"\n[2] 拉取历史 {fetch_start} ~ {fetch_end}...")
    panel_all = fetch_all_history(fetcher, ts_codes, fetch_start, fetch_end)
    panel_all["trade_date"] = pd.to_datetime(panel_all["trade_date"])

    # 3. 调仓日（训练/测试分别取）
    trade_dates = fetcher.get_trade_dates(fetch_start, fetch_end)
    train_rebal = get_rebal_dates(train_start, test_start, trade_dates,
                                  interval_weeks=args.rebal_weeks)
    test_rebal = get_rebal_dates(test_start, end_dt, trade_dates,
                                 interval_weeks=args.rebal_weeks)
    print(f"  训练调仓: {len(train_rebal)}, 测试调仓: {len(test_rebal)}")

    # 4. 训练集回测（旧权重 + 收集 IC）
    old_weights = _filter_tech_weights(get_factor_weights(args.strategy))
    print(f"\n[3] 训练集回测（旧权重） ...")
    train_metrics, _, _, ic_df = run_period_backtest(
        panel_all, train_rebal, old_weights,
        top_n_val=args.top, lookback=args.lookback,
        stoploss=args.stoploss, collect_ic=True)

    print(f"  训练绩效: 年化 {train_metrics['ann_return']*100:+.2f}%, "
          f"夏普 {train_metrics['sharpe']:.2f}, "
          f"回撤 {train_metrics['max_drawdown']*100:+.2f}%")

    # 5. 算训练 IC + 调权重
    if ic_df.empty:
        print("\n[!] 训练样本太少，无法算 IC")
        return
    ic_mean = ic_df.mean()
    print(f"\n[4] 训练集 IC 均值:")
    for k, v in ic_mean.sort_values(ascending=False).items():
        flag = " ★" if abs(v) >= 0.02 else ""
        print(f"    {k:<15} {v:>+.4f}{flag}")
    new_weights = adjust_weights_by_ic(old_weights, ic_mean, threshold=0.02)
    print(f"\n  权重调整对比:")
    for k in old_weights:
        if old_weights[k] != new_weights[k]:
            print(f"    {k:<15} {old_weights[k]:>5.2f} -> {new_weights[k]:.2f}")

    # 6. 测试集回测：旧权重 + 新权重，两条对比
    print(f"\n[5] 测试集回测（对照实验）...")
    test_old, _, _, _ = run_period_backtest(
        panel_all, test_rebal, old_weights,
        top_n_val=args.top, lookback=args.lookback, stoploss=args.stoploss)
    test_new, _, _, _ = run_period_backtest(
        panel_all, test_rebal, new_weights,
        top_n_val=args.top, lookback=args.lookback, stoploss=args.stoploss)

    # 7. 基准
    bm = fetch_benchmark(fetcher, test_rebal[0], test_rebal[-1])
    bm_ret = None
    if not bm.empty and test_rebal[0] in bm.index and test_rebal[-1] in bm.index:
        bm_ret = float(bm[test_rebal[-1]] / bm[test_rebal[0]] - 1)

    # 8. 对比报告
    print("\n" + "=" * 60)
    print("Walk-Forward 对比结果")
    print("=" * 60)
    fmt = lambda m: f"年化 {m['ann_return']*100:>+7.2f}%, " \
                    f"夏普 {m['sharpe']:>5.2f}, " \
                    f"回撤 {m['max_drawdown']*100:>+6.2f}%, " \
                    f"胜率 {m['win_rate']*100:>5.1f}%"
    print(f"  训练集 (旧权重):    {fmt(train_metrics)}")
    print(f"  测试集 (旧权重):    {fmt(test_old)}")
    print(f"  测试集 (新权重):    {fmt(test_new)}")
    if bm_ret is not None:
        print(f"  测试集 沪深300:    {bm_ret*100:+.2f}%")

    # 结论
    delta = test_new["ann_return"] - test_old["ann_return"]
    print()
    if delta > 0.01:
        print(f"  [结论] 按 IC 调权重在样本外**有效**（+{delta*100:.2f}% 年化提升）")
    elif delta < -0.01:
        print(f"  [结论] 按 IC 调权重在样本外**过拟合**（{delta*100:+.2f}% 年化恶化）")
    else:
        print(f"  [结论] 按 IC 调权重在样本外**无显著差异**（{delta*100:+.2f}%）")


if __name__ == "__main__":
    main()
