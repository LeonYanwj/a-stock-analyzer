import numpy as np
import pandas as pd


def calculate_metrics(portfolio_df, trades_df, initial_capital):
    """计算回测指标
    Returns:
        dict: 包含各项回测指标
    """
    if portfolio_df.empty:
        return {}

    values = portfolio_df["total_value"].values
    final_value = values[-1]
    total_return = (final_value - initial_capital) / initial_capital

    # 交易天数和年化
    n_days = len(values)
    annual_return = (1 + total_return) ** (252 / max(n_days, 1)) - 1

    # 最大回撤
    peak = np.maximum.accumulate(values)
    drawdown = (peak - values) / peak
    max_drawdown = np.max(drawdown)

    # 日收益率
    daily_returns = pd.Series(values).pct_change().dropna()

    # 夏普比率（无风险利率按年化 3% 计算）
    risk_free_daily = 0.03 / 252
    if daily_returns.std() > 0:
        sharpe = (daily_returns.mean() - risk_free_daily) / daily_returns.std() * np.sqrt(252)
    else:
        sharpe = 0.0

    # 胜率
    win_rate = 0.0
    if not trades_df.empty:
        sells = trades_df[trades_df["action"] == "SELL"]
        buys = trades_df[trades_df["action"] == "BUY"]
        if len(sells) > 0 and len(buys) >= len(sells):
            profits = []
            for i in range(len(sells)):
                profit = sells.iloc[i]["revenue"] - buys.iloc[i]["cost"]
                profits.append(profit)
            win_rate = sum(1 for p in profits if p > 0) / len(profits)

    return {
        "initial_capital": initial_capital,
        "final_value": round(final_value, 2),
        "total_return": round(total_return * 100, 2),
        "annual_return": round(annual_return * 100, 2),
        "max_drawdown": round(max_drawdown * 100, 2),
        "sharpe_ratio": round(sharpe, 2),
        "win_rate": round(win_rate * 100, 2),
        "total_trades": len(trades_df),
        "trading_days": n_days,
    }
