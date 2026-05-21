"""API 响应数据模型（Pydantic v2）"""
from datetime import date, datetime
from typing import Optional, List
from pydantic import BaseModel


# ===== 账户 =====
class AccountSummary(BaseModel):
    account_id: int
    account_name: str
    strategy_name: str
    initial_capital: float
    current_cash: float
    current_equity: float
    return_pct: float
    started_at: datetime
    is_active: int


class PositionRow(BaseModel):
    ts_code: str
    qty: int
    avg_cost: float
    current_price: Optional[float] = None
    return_pct: Optional[float] = None
    market_value: Optional[float] = None
    open_date: date
    price_source: Optional[str] = None       # realtime / close / cost


class TradeRow(BaseModel):
    trade_id: int
    trade_date: date
    side: str
    ts_code: str
    qty: int
    price: float
    amount: float
    commission: float
    reason: Optional[str] = None


class EquityPoint(BaseModel):
    trade_date: date
    cash: float
    market_value: float
    total_equity: float
    daily_return: Optional[float] = None


# ===== 选股 =====
class ScreenPick(BaseModel):
    ts_code: str
    name: Optional[str] = None
    score: float
    rank_num: int


class ScreenResult(BaseModel):
    strategy: str
    top_n: int
    picks: List[ScreenPick]


# ===== 评级 =====
class RatingFactor(BaseModel):
    key: str
    stars: Optional[int]
    desc: str


class RatingDimension(BaseModel):
    key: str
    label: str
    stars: Optional[float]
    weight: float
    factors: List[RatingFactor]


class StockRatingResp(BaseModel):
    ts_code: str
    name: str
    asof: str
    strategy: str
    overall_stars: Optional[float]
    grade: str
    dimensions: List[RatingDimension]


# ===== 回测 =====
class BacktestRun(BaseModel):
    run_id: int
    strategy_name: str
    start_date: date
    end_date: date
    ann_return: Optional[float] = None
    sharpe: Optional[float] = None
    max_drawdown: Optional[float] = None
    win_rate: Optional[float] = None
    n_periods: Optional[int] = None
    note: Optional[str] = None
    created_at: datetime


# ===== 股票 =====
class StockBasic(BaseModel):
    ts_code: str
    symbol: str
    name: str
    industry: Optional[str] = None
    list_date: Optional[date] = None
    is_active: int
    is_st: int


class DailyBar(BaseModel):
    trade_date: date
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    vol: Optional[float] = None
    pct_chg: Optional[float] = None
