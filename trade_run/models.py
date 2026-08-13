"""交易实例领域枚举与错误定义。"""
from enum import Enum


class RunStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    ENDED = "ended"
    DELETED = "deleted"


class PlanStatus(str, Enum):
    GENERATED = "generated"
    ELIGIBLE = "eligible"
    BLOCKED = "blocked"
    TRIGGERED = "triggered"
    PARTIALLY_FILLED = "partially_filled"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


STRATEGY_CODES = {"short_term", "medium_term", "long_term"}
ASSET_TYPES = {"stock", "etf"}
SIGNAL_SOURCES = {"legacy", "new"}
PLAN_WINDOWS = {"pre_market", "midday", "manual"}
RUNNABLE_STATUSES = {RunStatus.DRAFT.value, RunStatus.PAUSED.value}


class TradeRunError(Exception):
    """可稳定映射到 API 错误响应的领域错误。"""

    def __init__(self, code: str, message: str, status: int = 400, detail: str = None):
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail
        super().__init__(message)


def require(condition: bool, code: str, message: str, status: int = 400, detail: str = None):
    if not condition:
        raise TradeRunError(code, message, status, detail)
