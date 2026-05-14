import pandas as pd
from strategy.base import Strategy
from analysis.indicators import MA


class MACrossStrategy(Strategy):
    """均线交叉策略：短期均线上穿长期均线买入，下穿卖出"""

    def __init__(self, short_period=5, long_period=20):
        super().__init__(name=f"MA{short_period}xMA{long_period}")
        self.short_period = short_period
        self.long_period = long_period

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        ma_short = MA(df["close"], self.short_period)
        ma_long = MA(df["close"], self.long_period)

        signals = pd.Series(0, index=df.index)

        # 金叉：短期均线从下方穿越长期均线
        golden_cross = (ma_short > ma_long) & (ma_short.shift(1) <= ma_long.shift(1))
        # 死叉：短期均线从上方穿越长期均线
        death_cross = (ma_short < ma_long) & (ma_short.shift(1) >= ma_long.shift(1))

        signals[golden_cross] = 1
        signals[death_cross] = -1

        return signals
