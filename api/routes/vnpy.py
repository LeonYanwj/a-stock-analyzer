"""vn.py 运行时控制接口。

Token 只从项目 ``config.py`` 读取，接口不会接收或返回密钥。
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field

from api import tasks as task_mgr
from api.auth import require_trade_run_access
from api.errors import APIError
from vnpy_runtime import VnpyRuntime
from vnpy_runtime.strategy import equity_demo_strategy_info
from vnpy_runtime.history import sync_history
from vnpy_runtime.backtest import run_named_alpha_backtest


router = APIRouter(prefix="/api/vnpy", tags=["vnpy"])
runtime = VnpyRuntime()


class SubscribeRequest(BaseModel):
    vt_symbols: List[str] = Field(..., min_length=1)


class HistoryRequest(BaseModel):
    vt_symbols: List[str] = Field(..., min_length=1)
    start: datetime
    end: datetime
    lab_path: str = "data/vnpy_lab"


class BacktestRequest(BaseModel):
    vt_symbols: List[str] = Field(..., min_length=1)
    start: datetime
    end: datetime
    signal_name: str
    lab_path: str = "data/vnpy_lab"
    capital: float = Field(1_000_000, gt=0)
    strategy_setting: dict = Field(default_factory=dict)


def _access(request: Request, x_api_key: Optional[str]):
    return require_trade_run_access(request, x_api_key)


@router.get("/status")
def status(request: Request, x_api_key: Optional[str] = Header(None)):
    _access(request, x_api_key)
    return runtime.status()


@router.get("/strategies")
def strategies(request: Request, x_api_key: Optional[str] = Header(None)):
    _access(request, x_api_key)
    return {"strategies": [equity_demo_strategy_info()]}


@router.get("/ticks")
def ticks(request: Request, symbols: Optional[str] = None,
          x_api_key: Optional[str] = Header(None)):
    _access(request, x_api_key)
    requested = [item.strip() for item in symbols.split(",") if item.strip()] if symbols else None
    return {"ticks": runtime.ticks(requested)}


@router.post("/connect")
def connect(request: Request, x_api_key: Optional[str] = Header(None)):
    _access(request, x_api_key)
    try:
        return runtime.connect()
    except RuntimeError as exc:
        raise APIError("VNPY_CONNECT_FAILED", str(exc), 503) from exc


@router.post("/disconnect")
def disconnect(request: Request, x_api_key: Optional[str] = Header(None)):
    _access(request, x_api_key)
    return runtime.disconnect()


@router.post("/subscribe")
def subscribe(body: SubscribeRequest, request: Request,
              x_api_key: Optional[str] = Header(None)):
    _access(request, x_api_key)
    try:
        return runtime.subscribe(body.vt_symbols)
    except (RuntimeError, ValueError) as exc:
        raise APIError("VNPY_SUBSCRIBE_FAILED", str(exc), 503) from exc


@router.post("/history/sync")
def history_sync(body: HistoryRequest, request: Request,
                 x_api_key: Optional[str] = Header(None)):
    _access(request, x_api_key)
    if body.start >= body.end:
        raise APIError("VNPY_INVALID_HISTORY_RANGE", "历史数据起始时间必须早于结束时间", 400)
    try:
        return sync_history(runtime, body.vt_symbols, body.start, body.end, body.lab_path)
    except (RuntimeError, ValueError) as exc:
        raise APIError("VNPY_HISTORY_SYNC_FAILED", str(exc), 503) from exc


@router.post("/backtests")
def backtest(body: BacktestRequest, request: Request,
             x_api_key: Optional[str] = Header(None)):
    _access(request, x_api_key)
    if body.start >= body.end:
        raise APIError("VNPY_INVALID_BACKTEST_RANGE", "回测起始时间必须早于结束时间", 400)
    params = {
        "vt_symbols": body.vt_symbols,
        "start": body.start.isoformat(),
        "end": body.end.isoformat(),
        "signal_name": body.signal_name,
        "lab_path": body.lab_path,
        "capital": body.capital,
        "strategy": "EquityDemoStrategy",
    }
    task = task_mgr.submit(
        "vnpy_backtest", run_named_alpha_backtest,
        body.vt_symbols, body.start, body.end, body.signal_name,
        body.lab_path, body.capital, body.strategy_setting,
        params=params,
    )
    return {
        "task_id": task.task_id,
        "status": task.status,
        "task": task.to_dict(include_result=False),
        "tip": f"轮询 GET /api/tasks/{task.task_id} 查看回测进度和结果",
    }
