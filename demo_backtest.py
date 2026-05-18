"""离线回测演示（不依赖网络）

用 cache/ 中的历史日线跑 MA 交叉策略，
快速验证回测引擎参数（初始资金 / 佣金 / 不免5 / 滑点）的效果。

用法:
    python demo_backtest.py
"""
import sys
import io
import os
import glob

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import pandas as pd

from analysis.indicators import add_all_indicators
from strategy.ma_cross import MACrossStrategy
from backtest.engine import BacktestEngine


def find_cache_csv(ts_code: str = "000001.SZ") -> str:
    """挑一个匹配 ts_code 的历史日线缓存"""
    pattern = os.path.join("cache", f"daily_{ts_code}_*.csv")
    matches = sorted(glob.glob(pattern))
    if not matches:
        raise FileNotFoundError(
            f"找不到 {ts_code} 的缓存。先跑一次 main.py / screen.py 拉数据，"
            f"或把已有 CSV 放进 cache/。"
        )
    return matches[-1]  # 取最新的


def load_cache(path: str) -> pd.DataFrame:
    """读 cache CSV，统一 trade_date 为 datetime"""
    df = pd.read_csv(path)
    # 兼容两种 trade_date 格式：Tushare 字符串 'YYYYMMDD' 与 AKShare 'YYYY-MM-DD'
    df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str), errors="coerce")
    df = df.dropna(subset=["trade_date"]).sort_values("trade_date").reset_index(drop=True)
    return df


def main():
    cache_path = find_cache_csv("000001.SZ")
    df = load_cache(cache_path)

    print("=" * 60)
    print(f"离线回测演示  缓存: {os.path.basename(cache_path)}")
    print(f"数据范围: {df['trade_date'].min().date()} ~ "
          f"{df['trade_date'].max().date()}  ({len(df)} 行)")
    print("=" * 60)

    df = add_all_indicators(df)
    strategy = MACrossStrategy(short_period=5, long_period=20)
    signals = strategy.generate_signals(df)

    engine = BacktestEngine()
    print("\n回测参数:")
    print(f"  初始资金 = {engine.initial_capital:,.0f} 元")
    print(f"  佣金率   = {engine.commission*10000:.1f}/万")
    print(f"  最低佣金 = {engine.min_commission} 元 (不免5)")
    print(f"  滑点    = {engine.slippage:.1%}")

    result = engine.run(df, signals)
    m = result["metrics"]
    print("\n回测结果:")
    print(f"  初始资金:   {m['initial_capital']:>12,.2f}")
    print(f"  最终价值:   {m['final_value']:>12,.2f}")
    print(f"  总收益率:   {m['total_return']:>11}%")
    print(f"  年化收益:   {m['annual_return']:>11}%")
    print(f"  最大回撤:   {m['max_drawdown']:>11}%")
    print(f"  夏普:      {m['sharpe_ratio']:>11}")
    print(f"  胜率:      {m['win_rate']:>11}%")
    print(f"  交易次数:   {m['total_trades']:>11}")

    trades = result["trades"]
    if not trades.empty:
        print("\n交易记录（fee 列展示不免5 效果）:")
        show = trades.copy()
        for c in ["price", "fee", "cost", "revenue"]:
            if c in show.columns:
                show[c] = show[c].round(2)
        print(show.to_string(index=False))

        fees = trades["fee"]
        n_min = (fees == engine.min_commission).sum()
        n_rate = (fees > engine.min_commission).sum()
        total_fee = fees.sum()
        print(f"\n佣金统计:")
        print(f"  按最低 5 元收: {n_min} 笔")
        print(f"  按万一收:     {n_rate} 笔")
        print(f"  总佣金支出:   {total_fee:.2f} 元  "
              f"(占本金 {total_fee/engine.initial_capital:.2%})")


if __name__ == "__main__":
    main()
