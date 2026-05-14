import pandas as pd
import numpy as np
from config import DEFAULT_COMMISSION, DEFAULT_SLIPPAGE, DEFAULT_INITIAL_CAPITAL
from backtest.metrics import calculate_metrics


class BacktestEngine:
    """回测引擎"""

    def __init__(self, initial_capital=None, commission=None, slippage=None):
        self.initial_capital = initial_capital or DEFAULT_INITIAL_CAPITAL
        self.commission = commission or DEFAULT_COMMISSION
        self.slippage = slippage or DEFAULT_SLIPPAGE

    def run(self, df, signals):
        """执行回测
        Args:
            df: 行情数据，需包含 trade_date, open, close 列
            signals: 交易信号序列（1=买入, -1=卖出, 0=持有）
        Returns:
            result: 包含回测结果的字典
        """
        capital = self.initial_capital
        position = 0  # 持仓股数
        shares = 0
        trades = []
        portfolio_values = []

        for i in range(len(df)):
            date = df.iloc[i]["trade_date"]
            price = df.iloc[i]["close"]
            signal = signals.iloc[i]

            if signal == 1 and position == 0:
                # 买入：用全部资金买入
                buy_price = price * (1 + self.slippage)
                shares = int(capital / (buy_price * (1 + self.commission)) / 100) * 100
                if shares > 0:
                    cost = shares * buy_price * (1 + self.commission)
                    capital -= cost
                    position = 1
                    trades.append(
                        {
                            "date": date,
                            "action": "BUY",
                            "price": buy_price,
                            "shares": shares,
                            "cost": cost,
                        }
                    )

            elif signal == -1 and position == 1:
                # 卖出：清仓
                sell_price = price * (1 - self.slippage)
                revenue = shares * sell_price * (1 - self.commission)
                capital += revenue
                trades.append(
                    {
                        "date": date,
                        "action": "SELL",
                        "price": sell_price,
                        "shares": shares,
                        "revenue": revenue,
                    }
                )
                shares = 0
                position = 0

            # 记录每日组合价值
            total_value = capital + shares * price
            portfolio_values.append(
                {"date": date, "total_value": total_value, "cash": capital}
            )

        portfolio_df = pd.DataFrame(portfolio_values)
        trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()

        metrics = calculate_metrics(
            portfolio_df, trades_df, self.initial_capital
        )

        return {
            "portfolio": portfolio_df,
            "trades": trades_df,
            "metrics": metrics,
        }
