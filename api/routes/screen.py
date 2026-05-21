"""选股 API（同步 + 异步两种）"""
from typing import Optional
from fastapi import APIRouter, HTTPException

from screen import screen_market
from strategies import list_strategies, calc_optimal_top_n
from api.schemas import ScreenResult
from api import tasks as task_mgr


router = APIRouter(prefix="/api/screen", tags=["screen"])


def _screen_to_picks(df) -> dict:
    """把 DataFrame 转 picks JSON 结构"""
    if df.empty:
        return {"top_n": 0, "picks": []}
    picks = []
    for rank, (ts_code, row) in enumerate(df.iterrows(), 1):
        picks.append({
            "ts_code": ts_code,
            "name": row.get("name"),
            "score": float(row.get("score", 0)),
            "rank_num": rank,
        })
    return {"top_n": len(picks), "picks": picks}


def _do_screen(task, strategy, capital, top, limit, lookback, enable_news):
    """实际跑选股的函数（接受 task 参数用于进度报告）"""
    task.report(10, "拉取股票池...")
    df = screen_market(
        strategy=strategy, capital=capital, top_n_arg=top,
        lookback=lookback, limit=limit,
        enable_news=enable_news, verbose=False,
    )
    task.report(95, "整理结果...")
    return {
        "strategy": strategy,
        **_screen_to_picks(df),
    }


@router.get("", response_model=ScreenResult)
def run_screen_sync(
    strategy: str = "swing",
    capital: float = 0,
    top: int = 0,
    limit: int = 500,
    lookback: int = 60,
    enable_news: bool = False,
):
    """【同步】跑全市场选股，返回 Top N。

    ⚠️ 同步阻塞，limit 大时可能超过浏览器 30 秒超时。
    推荐网页使用：POST /api/tasks/screen 异步版本。
    """
    if strategy not in list_strategies():
        raise HTTPException(400, f"unknown strategy: {strategy}")
    try:
        df = screen_market(
            strategy=strategy, capital=capital, top_n_arg=top,
            lookback=lookback, limit=limit,
            enable_news=enable_news, verbose=False,
        )
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    return {"strategy": strategy, **_screen_to_picks(df)}


@router.post("/async")
def run_screen_async(
    strategy: str = "swing",
    capital: float = 0,
    top: int = 0,
    limit: int = 500,
    lookback: int = 60,
    enable_news: bool = False,
):
    """【异步】提交选股任务到后台，立即返回 task_id

    流程：
      1. POST /api/screen/async?strategy=swing&capital=100000
         → 返回 {"task_id": "xxx"}
      2. GET /api/tasks/{task_id}
         → 轮询查状态（pending → running → done/failed）
      3. status=done 时 result 字段含选股结果
    """
    if strategy not in list_strategies():
        raise HTTPException(400, f"unknown strategy: {strategy}")
    task = task_mgr.submit(
        "screen", _do_screen,
        strategy=strategy, capital=capital, top=top,
        limit=limit, lookback=lookback, enable_news=enable_news,
    )
    return {"task_id": task.task_id, "status": task.status,
            "tip": f"轮询 GET /api/tasks/{task.task_id}"}


@router.get("/strategies")
def list_strategies_api():
    """支持的策略列表"""
    return {"strategies": list_strategies()}


@router.get("/optimal-top-n")
def calc_top_n_api(capital: float):
    """根据资金量算建议持仓数"""
    return {"capital": capital, "top_n": calc_optimal_top_n(capital)}
