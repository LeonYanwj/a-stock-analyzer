from abc import ABC, abstractmethod
import pandas as pd


class Strategy(ABC):
    """策略基类"""

    def __init__(self, name="BaseStrategy"):
        self.name = name

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """生成交易信号
        Args:
            df: 包含行情数据和技术指标的 DataFrame
        Returns:
            信号序列：1=买入, -1=卖出, 0=持有
        """
        pass
