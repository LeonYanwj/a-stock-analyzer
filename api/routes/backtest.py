"""回测查询 API（不跑回测，只查 DB）"""
from typing import List, Optional
from fastapi import APIRouter, HTTPException

from data.db import get_conn, list_backtest_runs, get_backtest_detail
from api.schemas import BacktestRun


router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.get("", response_model=List[BacktestRun])
def list_runs(strategy: Optional[str] = None, limit: int = 50):
    """历史回测列表"""
    with get_conn() as conn:
        df = list_backtest_runs(conn, strategy_name=strategy, limit=limit)
    if df.empty:
        return []
    return df.to_dict("records")


@router.get("/{run_id}")
def get_run(run_id: int):
    """回测详情（含 equity / positions / ic）"""
    with get_conn() as conn:
        detail = get_backtest_detail(conn, run_id)
    if detail is None:
        raise HTTPException(404, "run_id 不存在")
    result = {
        "run": {k: (str(v) if hasattr(v, "isoformat") else float(v) if hasattr(v, "real") and not isinstance(v, (int, bool)) else v)
                for k, v in detail["run"].items()},
    }
    if not detail["equity"].empty:
        result["equity"] = detail["equity"].to_dict("records")
    if not detail["positions"].empty:
        result["positions"] = detail["positions"].head(100).to_dict("records")
    if not detail["ic"].empty:
        # IC 按因子聚合
        ic = detail["ic"]
        ic_summary = ic.groupby("factor_name")["ic"].agg(["mean", "std", "count"]).reset_index()
        ic_summary["ir"] = ic_summary["mean"] / ic_summary["std"]
        result["ic_summary"] = ic_summary.to_dict("records")
    return result
