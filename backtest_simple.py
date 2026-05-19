"""量价因子滑窗回测（默认半年）

设计：
- 只用 7 个量价因子（不依赖实时/历史不可得的数据源）：
  mom_30, reversal_5, low_vol, liquidity, macd_hist, macd_slope, pattern_score
- 调仓：每周一收盘选 Top N，等权重买入；下周一收盘卖出
- 交易成本：0.15%（万一佣金 + 千一滑点近似）
- 输出：组合净值曲线 + 年化收益 + 夏普 + 最大回撤 + 胜率，对照沪深 300

用法:
    python backtest_simple.py                       # 默认 6 个月、300 只
    python backtest_simple.py --months 6 --limit 500 --strategy swing
    python backtest_simple.py --strategy short_term --top 30
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

import numpy as np
import pandas as pd

from data.fetcher import DataFetcher
from universe import get_universe
from factors import compute_all_factors
from selector import score, top_n
from strategies import get_factor_weights, list_strategies


# 7 个量价因子（其他因子在历史回测中不可用，权重置 0）
TECH_ONLY_FACTORS = {
    "mom_30", "reversal_5", "low_vol", "liquidity",
    "macd_hist", "macd_slope", "pattern_score",
}

COMMISSION_TWO_WAY = 0.0015   # 双边交易成本（0.15%/笔，含滑点）


def _filter_tech_weights(weights: dict) -> dict:
    """从策略权重里只保留量价因子（其他置 0）"""
    return {k: v for k, v in weights.items() if k in TECH_ONLY_FACTORS}


def get_rebal_dates(start: datetime, end: datetime, trade_dates: list,
                    interval_weeks: int = 1) -> list:
    """取 [start, end] 内的调仓日：每 N 周的周一（停牌顺延）

    Args:
        interval_weeks: 调仓间隔（1=每周, 2=每两周, 4=每月）
    """
    trade_set = set(trade_dates)
    out = []
    cur = start
    while cur <= end:
        monday = cur - timedelta(days=cur.weekday())
        for delta in range(7):
            test = (monday + timedelta(days=delta)).strftime("%Y%m%d")
            if test in trade_set:
                if test not in out:
                    out.append(test)
                break
        cur += timedelta(days=7 * interval_weeks)
    return sorted(out)


def fetch_all_history(fetcher: DataFetcher, ts_codes: list,
                     start_date: str, end_date: str) -> pd.DataFrame:
    """一次性拉够所有股票从 start 到 end 的日线"""
    frames, fail = [], 0
    t0 = time.time()
    n = len(ts_codes)
    for i, tc in enumerate(ts_codes, 1):
        df = fetcher.get_daily(tc, start_date, end_date)
        if df is None or df.empty:
            fail += 1
        else:
            frames.append(df)
        if i % 100 == 0 or i == n:
            elapsed = time.time() - t0
            print(f"  [{i}/{n}]  耗时 {elapsed:.0f}s  失败 {fail}", flush=True)
    if not frames:
        raise RuntimeError("无任何历史数据")
    return pd.concat(frames, ignore_index=True)


def calc_metrics(equity: pd.Series, period_returns: pd.Series,
                 interval_weeks: int = 1) -> dict:
    """从净值序列计算绩效指标（年化考虑调仓间隔）

    interval_weeks: 调仓间隔（每 N 周一次），用于年化系数
    """
    if equity.empty:
        return {}
    total_ret = float(equity.iloc[-1] / equity.iloc[0] - 1)
    n_periods = len(period_returns)
    total_weeks = n_periods * interval_weeks
    if total_weeks > 0:
        ann_ret = (1 + total_ret) ** (52.0 / total_weeks) - 1
    else:
        ann_ret = 0.0
    # 夏普：按 period 计算，年化系数 = sqrt(52 / interval_weeks)
    periods_per_year = 52.0 / interval_weeks
    if period_returns.std() > 0:
        sharpe = (period_returns.mean() / period_returns.std()) * np.sqrt(periods_per_year)
    else:
        sharpe = 0.0
    rolling_max = equity.cummax()
    drawdown = equity / rolling_max - 1
    max_dd = float(drawdown.min())
    win_rate = float((period_returns > 0).sum() / max(1, n_periods))
    return {
        "total_return": total_ret,
        "ann_return":   ann_ret,
        "sharpe":       float(sharpe),
        "max_drawdown": max_dd,
        "win_rate":     win_rate,
        "n_periods":    n_periods,
        "total_weeks":  total_weeks,
    }


def fetch_benchmark(fetcher: DataFetcher, start_date: str, end_date: str) -> pd.Series:
    """拉沪深300指数日收盘价；失败返回空 Series"""
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol="sh000300")
        df["date"] = pd.to_datetime(df["date"])
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        df = df[(df["date"] >= start_dt) & (df["date"] <= end_dt)]
        df = df.sort_values("date").reset_index(drop=True)
        df["date_key"] = df["date"].dt.strftime("%Y%m%d")
        return pd.Series(df["close"].values, index=df["date_key"].values)
    except Exception as e:
        print(f"  [warn] 沪深300基准拉取失败: {type(e).__name__}: {str(e)[:80]}")
        return pd.Series(dtype=float)


def main():
    parser = argparse.ArgumentParser(description="半年量价因子回测")
    parser.add_argument("--months", type=int, default=12, help="回测月数（默认 12 = 1 年）")
    parser.add_argument("--limit", type=int, default=300, help="股票池规模（默认 300）")
    parser.add_argument("--top", type=int, default=50, help="每周选股数（默认 50）")
    parser.add_argument("--lookback", type=int, default=60, help="因子计算回看天数")
    parser.add_argument("--strategy", default="swing", choices=list_strategies())
    parser.add_argument("--rebal-weeks", type=int, default=1,
                        help="调仓间隔（周）：1=每周(短线), 2=每两周(波段), 4=每月(中长期)")
    parser.add_argument("--stoploss", type=float, default=-0.08,
                        help="持仓期止损线（默认 -8%）。设 0 关闭。")
    parser.add_argument("--ic", action="store_true",
                        help="输出因子 IC 分析（每个因子的 RankIC/IR）")
    args = parser.parse_args()

    print("=" * 60)
    print(f"回测: 策略={args.strategy}  月数={args.months}  Top={args.top}  股票池={args.limit}")
    print("=" * 60)

    fetcher = DataFetcher()
    end_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_dt = end_dt - timedelta(days=args.months * 31)
    # 历史数据起点 = 回测起点 - lookback buffer
    fetch_start = (start_dt - timedelta(days=args.lookback + 30)).strftime("%Y%m%d")
    fetch_end = end_dt.strftime("%Y%m%d")
    print(f"\n回测区间: {start_dt.date()} ~ {end_dt.date()}")
    print(f"数据拉取: {fetch_start} ~ {fetch_end}\n")

    # 1. 股票池
    print("[1/4] 筛选沪深主板股票池...")
    universe = get_universe(fetcher, exclude_st=True, min_list_days=0)
    universe = universe.head(args.limit)
    ts_codes = universe["ts_code"].tolist()
    print(f"  股票池: {len(ts_codes)} 只")

    # 2. 一次拉够所有历史数据（cache 命中后秒级）
    print(f"\n[2/4] 拉取 {len(ts_codes)} 只股票历史日线...")
    panel_all = fetch_all_history(fetcher, ts_codes, fetch_start, fetch_end)
    panel_all["trade_date"] = pd.to_datetime(panel_all["trade_date"])
    print(f"  面板: {len(panel_all)} 行")

    # 3. 计算调仓日（每周一，停牌顺延）
    print(f"\n[3/4] 计算调仓日历...")
    trade_dates = fetcher.get_trade_dates(fetch_start, fetch_end)
    rebal_dates = get_rebal_dates(start_dt, end_dt, trade_dates,
                                  interval_weeks=args.rebal_weeks)
    print(f"  调仓次数: {len(rebal_dates)} (每 {args.rebal_weeks} 周)")

    # 取量价因子权重
    full_weights = get_factor_weights(args.strategy)
    tech_weights = _filter_tech_weights(full_weights)
    print(f"  使用因子权重: {tech_weights}")

    # 4. 滑窗回测主循环
    print(f"\n[4/4] 滑窗回测... 止损线={args.stoploss*100:.0f}%")
    weekly_returns = []
    chosen_log = []
    stoploss_count = 0     # 累计触发止损股数
    ic_records = []        # 每期各因子的 IC（用于 IC 分析）
    equity = [1.0]
    factor_keys = list(tech_weights.keys())

    for i in range(len(rebal_dates) - 1):
        t = rebal_dates[i]
        t_next = rebal_dates[i + 1]
        t_dt = pd.to_datetime(t)
        cutoff_start = t_dt - timedelta(days=args.lookback + 30)
        window = panel_all[(panel_all["trade_date"] >= cutoff_start) &
                          (panel_all["trade_date"] <= t_dt)]
        if window["ts_code"].nunique() < 30:
            print(f"  {t}: 数据太少（{window['ts_code'].nunique()} 只），跳过")
            continue

        try:
            factors = compute_all_factors(window, asof_date=t)
            scored = score(factors, weights=tech_weights, min_valid_factors=3)
            picks = top_n(scored, n=args.top)
        except Exception as e:
            print(f"  {t}: 因子计算失败 {type(e).__name__}: {str(e)[:80]}")
            continue

        if picks.empty:
            continue
        picked_codes = picks.index.tolist()

        # 计算 [t, t_next] 各只的收益（含止损）
        rets = []
        period_returns_map = {}   # ts_code -> 周收益（含止损后）
        for tc in picked_codes:
            sw = panel_all[(panel_all["ts_code"] == tc) &
                          (panel_all["trade_date"] >= t_dt) &
                          (panel_all["trade_date"] <= pd.to_datetime(t_next))]
            if len(sw) < 2:
                continue
            close_start = sw.iloc[0]["close"]
            if pd.isna(close_start) or close_start <= 0:
                continue
            # 持仓期间逐日收益
            sw_ret = sw["close"] / close_start - 1
            if args.stoploss < 0:
                # 检查是否触发止损
                hit = sw_ret[sw_ret <= args.stoploss]
                if not hit.empty:
                    # 触发止损：按止损价 + 一点滑点平仓
                    ret = args.stoploss - 0.002
                    stoploss_count += 1
                else:
                    ret = sw.iloc[-1]["close"] / close_start - 1
            else:
                ret = sw.iloc[-1]["close"] / close_start - 1
            if pd.isna(ret):
                continue
            rets.append(ret)
            period_returns_map[tc] = ret

        if not rets:
            continue

        # 等权组合本期收益（扣双边手续费）
        port_ret = float(np.mean(rets)) - COMMISSION_TWO_WAY
        weekly_returns.append(port_ret)
        equity.append(equity[-1] * (1 + port_ret))
        chosen_log.append({"date": t, "n_picks": len(rets), "return": port_ret})

        # ---- IC 分析：本期各因子值 vs 全市场实际收益的秩相关 ----
        if args.ic:
            # 算全市场（不只是 picks）下周收益，与因子横截面做 RankIC
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
                            # 手动 rank+pearson = spearman 等价（不依赖 scipy）
                            f_rank = f_series.loc[common].rank()
                            r_rank = ret_series.loc[common].rank()
                            ic_row[fk] = float(f_rank.corr(r_rank, method="pearson"))
                ic_records.append(ic_row)

        print(f"  {t} -> {t_next}: 选 {len(picked_codes)} 只, 周收益 {port_ret*100:+.2f}%, "
              f"累计净值 {equity[-1]:.4f}")

    if not weekly_returns:
        print("\n[error] 回测无有效周次")
        return

    # 5. 算指标
    weekly_returns = pd.Series(weekly_returns)
    equity_s = pd.Series(equity)
    metrics = calc_metrics(equity_s, weekly_returns, interval_weeks=args.rebal_weeks)

    # 6. 基准（沪深 300）
    bm = fetch_benchmark(fetcher, rebal_dates[0], rebal_dates[-1])
    bm_ret = None
    if not bm.empty and rebal_dates[0] in bm.index and rebal_dates[-1] in bm.index:
        bm_ret = float(bm[rebal_dates[-1]] / bm[rebal_dates[0]] - 1)

    print("\n" + "=" * 60)
    print(f"回测结果（策略 {args.strategy}, {metrics['n_periods']} 次调仓 / "
          f"{metrics['total_weeks']} 周, 持仓 {args.top} 只）")
    print("=" * 60)
    print(f"  总收益:       {metrics['total_return']*100:+.2f}%")
    print(f"  年化收益:     {metrics['ann_return']*100:+.2f}%")
    print(f"  夏普比率:     {metrics['sharpe']:.2f}")
    print(f"  最大回撤:     {metrics['max_drawdown']*100:+.2f}%")
    print(f"  胜率:         {metrics['win_rate']*100:.1f}%")
    if args.stoploss < 0:
        print(f"  止损触发:     {stoploss_count} 次（阈值 {args.stoploss*100:.0f}%）")
    if bm_ret is not None:
        excess = metrics["total_return"] - bm_ret
        print(f"  ---")
        print(f"  沪深300同期: {bm_ret*100:+.2f}%")
        print(f"  超额收益:     {excess*100:+.2f}%")

    # IC 分析输出
    if args.ic and ic_records:
        ic_df = pd.DataFrame(ic_records).set_index("date")
        print("\n" + "=" * 60)
        print("因子 IC 分析（RankIC，正值=因子大者未来收益高）")
        print("=" * 60)
        print(f"{'因子':<15} {'IC均值':>10} {'IC标准差':>10} {'IR':>8} {'IC>0比例':>10}")
        ic_summary = []
        for fk in factor_keys:
            if fk in ic_df.columns:
                s = ic_df[fk].dropna()
                if len(s) >= 3:
                    mean = s.mean()
                    std = s.std()
                    ir = mean / std if std > 0 else 0
                    win = (s > 0).sum() / len(s)
                    ic_summary.append((fk, mean, std, ir, win))
                    flag = " ★" if abs(mean) >= 0.03 else ""
                    print(f"{fk:<15} {mean:>+10.4f} {std:>10.4f} {ir:>+8.2f} {win*100:>9.1f}%{flag}")
        print("\n  解读：IC|均值| >= 0.03 算有效（带★），IR >= 0.5 算稳定")

    # 保存结果
    os.makedirs("output", exist_ok=True)
    result_df = pd.DataFrame({
        "date": [rebal_dates[i + 1] for i in range(len(weekly_returns))],
        "weekly_return": weekly_returns.values,
        "equity": [equity[i + 1] for i in range(len(weekly_returns))],
    })
    out_path = os.path.join(
        "output",
        f"backtest_{args.strategy}_{args.months}m_r{args.rebal_weeks}w_{fetch_end}.csv",
    )
    result_df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n详细结果: {out_path}")


if __name__ == "__main__":
    main()
