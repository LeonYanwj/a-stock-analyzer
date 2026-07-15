"""自选股 CRUD 与日报任务。"""
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from api import tasks as task_mgr
from api.errors import NotFound
from strategies import list_strategies
import watchlist as watchlist_store


router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class WatchlistIn(BaseModel):
    code: str
    name: Optional[str] = None
    group_name: str = "默认"
    strategy: str = "swing"
    note: Optional[str] = None


class WatchlistUpdate(BaseModel):
    name: Optional[str] = None
    group_name: Optional[str] = None
    strategy: Optional[str] = None
    note: Optional[str] = None
    is_active: Optional[bool] = None


@router.get("")
def list_watchlist(active_only: bool = True):
    return watchlist_store.list_all(active_only=active_only).to_dict("records")


@router.post("")
def add_watchlist(body: WatchlistIn):
    if body.strategy not in list_strategies():
        from api.errors import BadRequest
        raise BadRequest(f"未知策略：{body.strategy}", code="UNKNOWN_STRATEGY")
    try:
        watch_id = watchlist_store.add(
            body.code, body.name, body.group_name, body.strategy, body.note)
    except ValueError as e:
        from api.errors import BadRequest
        raise BadRequest(str(e), code="INVALID_STOCK_CODE")
    return {"watch_id": watch_id}


@router.put("/{watch_id}")
def update_watchlist(watch_id: int, body: WatchlistUpdate):
    values = body.dict(exclude_none=True)
    if not values:
        from api.errors import BadRequest
        raise BadRequest("至少提供一个需要修改的字段", code="EMPTY_UPDATE")
    if values.get("strategy") and values["strategy"] not in list_strategies():
        from api.errors import BadRequest
        raise BadRequest(f"未知策略：{values['strategy']}", code="UNKNOWN_STRATEGY")
    if not watchlist_store.update(watch_id, **values):
        raise NotFound(f"自选股记录 {watch_id} 不存在", code="WATCHLIST_NOT_FOUND")
    return {"updated": True, "watch_id": watch_id}


@router.delete("/{watch_id}")
def delete_watchlist(watch_id: int):
    if not watchlist_store.delete(watch_id):
        raise NotFound(f"自选股记录 {watch_id} 不存在", code="WATCHLIST_NOT_FOUND")
    return {"deleted": True, "watch_id": watch_id}


def _run_report(task, send: bool):
    task.report(10, "准备自选股数据...")
    import watchlist_analyzer
    result = watchlist_analyzer.analyze(send=send)
    task.report(95, "整理邮件结果...")
    result.pop("html", None)
    return result


@router.post("/report/async")
def run_report_async(send: bool = True):
    task = task_mgr.submit(
        "watchlist_report", _run_report,
        params={"send": send}, send=send)
    return {"task_id": task.task_id, "status": task.status}
