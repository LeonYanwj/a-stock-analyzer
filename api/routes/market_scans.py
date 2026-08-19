"""独立市场扫描 API：产生候选池，不依赖交易实例。"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel, Field

from api import tasks as task_mgr
from api.auth import require_trade_run_access
from api.errors import APIError
from api.routes.trade_runs import get_service
from trade_run.market_scan import (get_market_scan_task, list_market_scan_tasks,
                                   submit_market_scan)
from trade_run.models import TradeRunError


router = APIRouter(prefix="/api/market-scans", tags=["market-scans"])


class MarketScanRequest(BaseModel):
    strategy_code: str
    asset_types: List[str] = Field(..., min_length=1)
    plan_window: str
    stock_scope: str = "quick"
    quick_limit: int = Field(100, ge=50, le=500)
    as_of: Optional[datetime] = None


def _require_access(request: Request, x_api_key: Optional[str]):
    return require_trade_run_access(request, x_api_key)


@router.post("")
def submit_scan(body: MarketScanRequest, request: Request,
                x_api_key: Optional[str] = Header(None)):
    """提交独立后台扫描；不会读取或改变任何交易实例。"""
    _require_access(request, x_api_key)
    try:
        task = submit_market_scan(
            task_mgr, get_service(), body.strategy_code, body.asset_types,
            body.plan_window, body.as_of or datetime.now(),
            stock_scope=body.stock_scope, quick_limit=body.quick_limit,
        )
    except TradeRunError as exc:
        raise APIError(exc.code, exc.message, exc.status, exc.detail)
    return {
        "task_id": task.task_id,
        "status": task.status,
        "task": task.to_dict(include_result=False),
        "tip": f"轮询 GET /api/market-scans/{task.task_id} 查看进度",
    }


@router.get("")
def list_scans(request: Request, limit: int = Query(30, ge=1, le=100),
               x_api_key: Optional[str] = Header(None)):
    """列出全部独立市场扫描任务记录。"""
    _require_access(request, x_api_key)
    return list_market_scan_tasks(task_mgr, limit)


@router.get("/{task_id}")
def get_scan(task_id: str, request: Request,
             x_api_key: Optional[str] = Header(None)):
    """查询扫描任务的实时进度、阶段时间线和完成后的候选池。"""
    _require_access(request, x_api_key)
    task = get_market_scan_task(task_mgr, task_id)
    if task is None:
        raise APIError("MARKET_SCAN_TASK_NOT_FOUND", "市场扫描任务不存在", 404)
    return task
