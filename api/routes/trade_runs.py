"""新交易实例 API。

首期执行方式为 manual：计划供用户在券商客户端照抄，实际成交由用户回填。
本模块不调用行情网站、不自动提交券商订单。
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel, Field

from api.errors import APIError
from api.auth import require_trade_run_access
from trade_run.models import TradeRunError
from trade_run.repository import MySqlTradeRunRepository
from trade_run.service import TradeRunService
from trade_run.planner import TradeRunPlanner
from trade_run.market_scan import (get_market_scan_task, list_market_scan_tasks,
                                   submit_market_scan)
from api import tasks as task_mgr


router = APIRouter(prefix="/api/trade-runs", tags=["trade-runs"])
system_router = APIRouter(prefix="/api/system", tags=["system"])
dashboard_router = APIRouter(tags=["dashboard"])

# 路由不使用内存数据库承载交易历史。服务在应用启动时配置 MySQL；缺配置或
# 未执行迁移时，接口明确报错，避免把临时数据伪装成用户可依赖的交易记录。
_service = None


def get_service() -> TradeRunService:
    if _service is None:
        raise APIError("TRADE_RUN_NOT_CONFIGURED", "交易实例数据库尚未配置；请先执行 sql/trade_run_schema.sql 并设置 config.py", 503)
    return _service


def is_configured() -> bool:
    return _service is not None


def configure_service(service: TradeRunService):
    """供集成测试或生产启动器显式注入持久化服务。"""
    global _service
    _service = service


def configure_mysql_service():
    repo = MySqlTradeRunRepository.from_config()
    repo.initialize()
    configure_service(TradeRunService(repo))


class CreateTradeRunRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    strategy_code: str
    capital: float = Field(..., gt=0)
    max_position_pct: float = Field(..., gt=0, le=1)
    asset_types: List[str] = Field(..., min_length=1)
    signal_source: str
    shadow_signal_source: Optional[str] = None
    plan_windows: List[str] = Field(default_factory=lambda: ["pre_market", "midday"])


class StopTradeRunRequest(BaseModel):
    action: str = "pause"
    reason: str = "用户手动停止"


class CreatePlanRequest(BaseModel):
    ts_code: str
    asset_type: str
    side: str
    suggested_qty: int
    reference_price: float
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    data_status: str = "delayed"
    blocked_reason: Optional[str] = None
    valid_from: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    reason: str = "待策略服务写入理由"
    evidence: Dict[str, Any] = Field(default_factory=dict)


class RecordFillRequest(BaseModel):
    idempotency_key: str = Field(..., min_length=1, max_length=100)
    ts_code: str
    side: str
    qty: int
    price: float
    fee: float = 0
    executed_at: datetime
    plan_id: Optional[int] = None
    asset_type: Optional[str] = None
    source: str = "manual"
    note: Optional[str] = None
    broker_quote_confirmed: bool = False
    quote_checked_at: Optional[datetime] = None


class GeneratePlansRequest(BaseModel):
    plan_window: str
    as_of: Optional[datetime] = None


class MarketScanRequest(BaseModel):
    plan_window: str
    as_of: Optional[datetime] = None


def require_trade_run_api_key(request: Request, x_api_key: Optional[str] = Header(None)):
    """兼容旧名称：会话 Cookie 与 X-API-Key 都可通过。"""
    return require_trade_run_access(request, x_api_key)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except TradeRunError as exc:
        raise APIError(exc.code, exc.message, exc.status, exc.detail)


@router.get("/strategy-definitions", dependencies=[])
def list_strategy_definitions(request: Request, x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(request, x_api_key)
    return get_service().repo.list_strategies()


@router.get("/strategy-definitions/{code}/versions")
def list_strategy_versions(code: str, request: Request, x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(request, x_api_key)
    versions = get_service().repo.list_versions(code)
    if not versions:
        raise APIError("STRATEGY_NOT_FOUND", "策略不存在", 404)
    return versions


@router.post("")
def create_trade_run(body: CreateTradeRunRequest, request: Request, x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(request, x_api_key)
    return _call(get_service().create_run, body.name, body.strategy_code, body.capital, body.max_position_pct, body.asset_types, body.signal_source, body.shadow_signal_source, body.plan_windows)


@router.get("")
def list_trade_runs(request: Request, include_deleted: bool = False, x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(request, x_api_key)
    return _call(get_service().list_runs, include_deleted)


@router.get("/dashboard")
def dashboard(request: Request, x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(request, x_api_key)
    return _call(get_service().dashboard)


@dashboard_router.get("/api/dashboard")
def root_dashboard(request: Request, x_api_key: Optional[str] = Header(None)):
    """前端首页的交易实例概览。"""
    require_trade_run_api_key(request, x_api_key)
    return _call(get_service().dashboard)


@router.get("/{run_id}")
def get_trade_run(run_id: int, request: Request, x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(request, x_api_key)
    return _call(get_service().get_run, run_id)


@router.post("/{run_id}/start")
def start_trade_run(run_id: int, request: Request, x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(request, x_api_key)
    return _call(get_service().start_run, run_id)


@router.post("/{run_id}/stop")
def stop_trade_run(run_id: int, body: StopTradeRunRequest, request: Request, x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(request, x_api_key)
    return _call(get_service().stop_run, run_id, body.action, body.reason)


@router.delete("/{run_id}")
def delete_trade_run(run_id: int, request: Request, x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(request, x_api_key)
    return _call(get_service().delete_run, run_id)


@router.get("/{run_id}/dashboard")
def trade_run_dashboard(run_id: int, request: Request, x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(request, x_api_key)
    return _call(get_service().dashboard, run_id)


@router.post("/{run_id}/plans")
def create_plan(run_id: int, body: CreatePlanRequest, request: Request, x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(request, x_api_key)
    return _call(get_service().create_plan, run_id, body.ts_code, body.asset_type, body.side,
                 body.suggested_qty, body.reference_price, body.min_price, body.max_price,
                 body.data_status, body.blocked_reason,
                 body.valid_from.isoformat() if body.valid_from else None,
                 body.expires_at.isoformat() if body.expires_at else None,
                 body.reason, body.evidence)


@router.get("/{run_id}/plans")
def list_plans(run_id: int, request: Request, x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(request, x_api_key)
    return _call(get_service().list_plans, run_id)


@router.get("/{run_id}/plans/{plan_id}")
def get_plan(run_id: int, plan_id: int, request: Request, x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(request, x_api_key)
    return _call(get_service().get_plan, run_id, plan_id)


@router.post("/{run_id}/fills")
def record_fill(run_id: int, body: RecordFillRequest, request: Request, x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(request, x_api_key)
    return _call(get_service().record_fill, run_id,
                 idempotency_key=body.idempotency_key, ts_code=body.ts_code, side=body.side,
                 qty=body.qty, price=body.price, fee=body.fee,
                 executed_at=body.executed_at.isoformat(), plan_id=body.plan_id,
                 asset_type=body.asset_type,
                 source=body.source, note=body.note,
                 broker_quote_confirmed=body.broker_quote_confirmed,
                 quote_checked_at=body.quote_checked_at.isoformat() if body.quote_checked_at else None)


@router.get("/{run_id}/positions")
def get_positions(run_id: int, request: Request, x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(request, x_api_key)
    return _call(get_service().positions, run_id)


@router.get("/{run_id}/performance")
def get_performance(run_id: int, request: Request, x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(request, x_api_key)
    return _call(get_service().performance, run_id)


@router.get("/{run_id}/events")
def get_events(run_id: int, request: Request, limit: int = Query(50, ge=1, le=200), x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(request, x_api_key)
    return _call(get_service().events, run_id, limit)


@router.post("/{run_id}/generate-plans")
def generate_plans(run_id: int, body: GeneratePlansRequest, request: Request, x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(request, x_api_key)
    return _call(TradeRunPlanner(get_service()).generate, run_id, body.plan_window, body.as_of or datetime.now())


@router.post("/{run_id}/market-scans")
def submit_scan(run_id: int, body: MarketScanRequest, request: Request,
                x_api_key: Optional[str] = Header(None)):
    """提交一次后台市场扫描。

    接口立即返回 ``task_id``。前端随后轮询本模块的任务详情接口，可持续展示
    ``pending/running/done/failed``、0-100 进度和当前阶段；扫描结果仅为候选池，
    不会生成交易计划或改动任何账务。
    """
    require_trade_run_api_key(request, x_api_key)
    try:
        task = submit_market_scan(task_mgr, get_service(), run_id, body.plan_window,
                                  body.as_of or datetime.now())
    except TradeRunError as exc:
        raise APIError(exc.code, exc.message, exc.status, exc.detail)
    return {
        "task_id": task.task_id,
        "status": task.status,
        "task": task.to_dict(include_result=False),
        "tip": f"轮询 GET /api/trade-runs/{run_id}/market-scans/{task.task_id} 查看进度",
    }


@router.get("/{run_id}/market-scans")
def list_scans(run_id: int, request: Request, limit: int = Query(30, ge=1, le=100),
               x_api_key: Optional[str] = Header(None)):
    """列出当前交易实例的市场扫描任务记录。"""
    require_trade_run_api_key(request, x_api_key)
    _call(get_service().get_run, run_id)
    return list_market_scan_tasks(task_mgr, run_id, limit)


@router.get("/{run_id}/market-scans/{task_id}")
def get_scan(run_id: int, task_id: str, request: Request,
             x_api_key: Optional[str] = Header(None)):
    """查询市场扫描任务的实时进度和完成后的候选池结果。"""
    require_trade_run_api_key(request, x_api_key)
    _call(get_service().get_run, run_id)
    task = get_market_scan_task(task_mgr, run_id, task_id)
    if task is None:
        raise APIError("MARKET_SCAN_TASK_NOT_FOUND", "该交易实例下不存在此市场扫描任务", 404)
    return task


@router.get("/{run_id}/comparison")
def get_comparison(run_id: int, request: Request, x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(request, x_api_key)
    return _call(get_service().comparisons, run_id)


@system_router.get("/data-status")
def data_status(request: Request, x_api_key: Optional[str] = Header(None)):
    """首期的数据能力声明，避免把免费/延迟数据误认为实时可下单行情。"""
    require_trade_run_api_key(request, x_api_key)
    configured = _service is not None
    return {
        "trading_mode": "manual_fill",
        "broker_order_submission": False,
        "data_provider_state": "not_configured" if not configured else "manual_plan_only",
        "quote_reliability": "not_realtime",
        "message": "当前仅支持人工照抄计划并回填实际成交；免费数据不能作为可靠实时下单依据。",
    }
