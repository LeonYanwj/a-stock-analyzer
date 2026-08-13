"""以交易实例为中心的手工执行核。

本包不依赖旧模拟盘账务。首期只记录计划与用户确认的实际成交，
为后续策略调度和券商适配器保留同一条交易链路。
"""

from .service import TradeRunService

__all__ = ["TradeRunService"]
