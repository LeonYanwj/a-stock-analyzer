"""
A股股票分析与量化回测系统
用法: python main.py
"""
import sys
import io

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from datetime import datetime
from data.fetcher import DataFetcher
from analysis.indicators import add_all_indicators
from strategy.ma_cross import MACrossStrategy
from backtest.engine import BacktestEngine
from utils.plot import plot_kline_with_indicators, plot_backtest_result


def main():
    # 1. 获取数据
    print("=" * 50)
    print("A股股票分析与量化回测系统")
    print("=" * 50)

    fetcher = DataFetcher()

    ts_code = "000001.SZ"  # 平安银行
    start_date = "20240101"
    end_date = datetime.now().strftime("%Y%m%d")

    print(f"\n正在获取 {ts_code} 的日K线数据...")
    df = fetcher.get_daily(ts_code, start_date, end_date)
    print(f"获取到 {len(df)} 条数据")

    # 2. 计算技术指标
    print("\n正在计算技术指标...")
    df = add_all_indicators(df)
    print("技术指标计算完成: MA, MACD, RSI, KDJ, BOLL")

    # 3. 绘制K线图
    print("\n绘制K线与技术指标图...")
    plot_kline_with_indicators(df, title=f"{ts_code} K线与技术指标")

    # 4. 策略回测
    print("\n开始均线交叉策略回测...")
    strategy = MACrossStrategy(short_period=5, long_period=20)
    signals = strategy.generate_signals(df)

    engine = BacktestEngine()
    result = engine.run(df, signals)

    # 5. 输出回测结果
    metrics = result["metrics"]
    print("\n" + "=" * 50)
    print(f"策略: {strategy.name}")
    print(f"回测区间: {start_date} ~ {end_date}")
    print("-" * 50)
    print(f"初始资金:     {metrics['initial_capital']:>12,.2f} 元")
    print(f"最终价值:     {metrics['final_value']:>12,.2f} 元")
    print(f"总收益率:     {metrics['total_return']:>11}%")
    print(f"年化收益率:   {metrics['annual_return']:>11}%")
    print(f"最大回撤:     {metrics['max_drawdown']:>11}%")
    print(f"夏普比率:     {metrics['sharpe_ratio']:>11}")
    print(f"胜率:         {metrics['win_rate']:>11}%")
    print(f"交易次数:     {metrics['total_trades']:>11}")
    print(f"交易天数:     {metrics['trading_days']:>11}")
    print("=" * 50)

    # 6. 绘制回测净值曲线
    print("\n绘制回测净值曲线...")
    plot_backtest_result(result["portfolio"], title=f"{strategy.name} 回测净值曲线")

    # 7. 输出交易记录
    if not result["trades"].empty:
        print("\n交易记录:")
        print(result["trades"].to_string(index=False))


if __name__ == "__main__":
    main()
