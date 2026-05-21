"""选股 API（同步调用，可能耗时较长）"""
from typing import Optional
from fastapi import APIRouter, HTTPException

from screen import screen_market
from strategies import list_strategies, calc_optimal_top_n
from api.schemas import ScreenResult


router = APIRouter(prefix="/api/screen", tags=["screen"])


@router.get("", response_model=ScreenResult)
def run_screen(
    strategy: str = "swing",
    capital: float = 0,
    top: int = 0,
    limit: int = 500,
    lookback: int = 60,
    enable_news: bool = False,
):
    """跑全市场选股，返回 Top N

    注意：此接口同步阻塞，可能耗时 1-10 分钟。
    生产环境建议异步化（celery / arq）。
    """
    if strategy not in list_strategies():
        raise HTTPException(400, f"unknown strategy: {strategy}")
    try:
        df = screen_market(
            strategy=strategy,
            capital=capital,
            top_n_arg=top,
            lookback=lookback,
            limit=limit,
            enable_news=enable_news,
            verbose=False,
        )
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    if df.empty:
        return {"strategy": strategy, "top_n": 0, "picks": []}
    picks = []
    for rank, (ts_code, row) in enumerate(df.iterrows(), 1):
        picks.append({
            "ts_code": ts_code,
            "name": row.get("name"),
            "score": float(row.get("score", 0)),
            "rank_num": rank,
        })
    return {"strategy": strategy, "top_n": len(picks), "picks": picks}


@router.get("/strategies")
def list_strategies_api():
    """支持的策略列表"""
    return {"strategies": list_strategies()}


@router.get("/optimal-top-n")
def calc_top_n_api(capital: float):
    """根据资金量算建议持仓数"""
    return {"capital": capital, "top_n": calc_optimal_top_n(capital)}
