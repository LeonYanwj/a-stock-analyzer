"""ETF 白名单、数据准备与流动性状态管理；不提供自动下单。"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel, Field

from api import tasks as task_mgr
from api.etf_history import (build_history_window, normalize_etf_code,
                             run_etf_history_sync)
from api.errors import APIError
from api.routes.trade_runs import require_trade_run_api_key
from data.db import get_conn


router = APIRouter(prefix="/api/etfs", tags=["etfs"])


class CreateEtfRequest(BaseModel):
    """登记 ETF；可用同一接口显式更新其基础资料。"""
    ts_code: str = Field(..., min_length=9, max_length=16)
    symbol: str = Field(..., min_length=6, max_length=10)
    name: str = Field(..., min_length=1, max_length=64)
    etf_type: Optional[str] = Field(None, max_length=32)
    tracking_index: Optional[str] = Field(None, max_length=128)
    listing_status: Optional[str] = Field(None, min_length=1, max_length=16)
    whitelist: Optional[bool] = None
    avg_amount: Optional[float] = Field(None, ge=0)


class UpdateEtfRequest(BaseModel):
    """仅更新明确传入的可维护字段，尤其是白名单开关。"""
    symbol: Optional[str] = Field(None, min_length=6, max_length=10)
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    etf_type: Optional[str] = Field(None, max_length=32)
    tracking_index: Optional[str] = Field(None, max_length=128)
    listing_status: Optional[str] = Field(None, min_length=1, max_length=16)
    whitelist: Optional[bool] = None
    avg_amount: Optional[float] = Field(None, ge=0)


class EtfHistorySyncRequest(BaseModel):
    start_date: Optional[date] = None
    end_date: Optional[date] = None


def _normalize_request_code(ts_code: str) -> str:
    try:
        return normalize_etf_code(ts_code)
    except ValueError as exc:
        raise APIError("INVALID_ETF_CODE", str(exc), 422) from exc


def _storage_error() -> APIError:
    """不要把 MySQL 主机、账号或 SQL 文本返回给浏览器。"""
    return APIError("ETF_STORAGE_UNAVAILABLE", "ETF 数据服务暂不可用", 503)


def _prepare_update_values(updates: dict) -> dict:
    """规范化动态 UPDATE 参数，字段名始终来自 Pydantic 模型而非客户端。"""
    values = dict(updates)
    for field in ("symbol", "name", "listing_status", "whitelist"):
        if field in values and values[field] is None:
            raise APIError("INVALID_ETF_UPDATE", f"{field} 不能为 null", 422)
    for field in ("symbol", "name", "listing_status"):
        if field in values:
            values[field] = values[field].strip()
            if not values[field]:
                raise APIError("INVALID_ETF_UPDATE", f"{field} 不能为空白", 422)
    return values


def _status_from_conn(conn, ts_code: str) -> Optional[dict]:
    sql = """
        SELECT b.ts_code, b.symbol, b.name, b.etf_type, b.tracking_index,
               b.listing_status, b.whitelist, b.avg_amount, b.updated_at,
               COUNT(d.trade_date) AS daily_count,
               MIN(d.trade_date) AS first_trade_date,
               MAX(d.trade_date) AS last_trade_date
        FROM market_etf_basic b
        LEFT JOIN market_etf_daily d ON d.ts_code=b.ts_code
        WHERE b.ts_code=%s
        GROUP BY b.ts_code, b.symbol, b.name, b.etf_type, b.tracking_index,
                 b.listing_status, b.whitelist, b.avg_amount, b.updated_at
    """
    cur = conn.cursor()
    try:
        cur.execute(sql, (ts_code,))
        row = cur.fetchone()
        if not row:
            return None
        columns = [column[0] for column in cur.description]
        result = dict(zip(columns, row))
    finally:
        cur.close()
    result["whitelist"] = bool(result["whitelist"])
    result["daily_count"] = int(result["daily_count"] or 0)
    if result["daily_count"] == 0:
        result["data_state"] = "history_missing"
        result["scan_ready"] = False
    elif result["daily_count"] < 21:
        result["data_state"] = "insufficient_history"
        result["scan_ready"] = False
    elif result["listing_status"] != "active":
        result["data_state"] = "inactive"
        result["scan_ready"] = False
    elif not result["whitelist"]:
        result["data_state"] = "not_whitelisted"
        result["scan_ready"] = False
    else:
        result["data_state"] = "ready"
        result["scan_ready"] = True
    return result


def _require_registered_etf(ts_code: str) -> dict:
    try:
        with get_conn() as conn:
            status = _status_from_conn(conn, ts_code)
    except Exception as exc:
        raise _storage_error() from exc
    if status is None:
        raise APIError("ETF_NOT_FOUND", "ETF 尚未登记；请先调用 POST /api/etfs", 404)
    return status


@router.get("")
def list_etfs(request: Request, search: Optional[str] = None, etf_type: Optional[str] = None,
              whitelist_only: bool = True, include_inactive: bool = False,
              limit: int = Query(200, ge=1, le=500),
              x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(request, x_api_key)
    sql = "SELECT ts_code,symbol,name,etf_type,tracking_index,listing_status,whitelist,avg_amount,updated_at FROM market_etf_basic"
    conditions, params = [], []
    if not include_inactive:
        conditions.append("listing_status='active'")
    if whitelist_only:
        conditions.append("whitelist=1")
    if etf_type:
        conditions.append("etf_type=%s")
        params.append(etf_type)
    if search:
        conditions.append("(ts_code LIKE %s OR symbol LIKE %s OR name LIKE %s OR tracking_index LIKE %s)")
        params.extend([f"%{search}%"] * 4)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY avg_amount DESC, ts_code LIMIT %s"
    params.append(limit)
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            columns = [x[0] for x in cur.description]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]
            cur.close()
        for row in rows:
            row["whitelist"] = bool(row["whitelist"])
        return rows
    except Exception as exc:
        raise _storage_error() from exc


@router.post("")
def create_etf(body: CreateEtfRequest, request: Request,
               x_api_key: Optional[str] = Header(None)):
    """登记一只 ETF；未显式传 ``whitelist=true`` 时不会进入扫描范围。"""
    require_trade_run_api_key(request, x_api_key)
    ts_code = _normalize_request_code(body.ts_code)
    symbol, name = body.symbol.strip(), body.name.strip()
    if not symbol or not name:
        raise APIError("INVALID_ETF_CREATE", "symbol 和 name 不能为空白", 422)
    listing_status = body.listing_status.strip() if body.listing_status else "active"
    if not listing_status:
        raise APIError("INVALID_ETF_CREATE", "listing_status 不能为空白", 422)
    try:
        with get_conn() as conn:
            existing = _status_from_conn(conn, ts_code)
            if existing is None:
                cur = conn.cursor()
                try:
                    cur.execute(
                        """INSERT INTO market_etf_basic
                           (ts_code,symbol,name,etf_type,tracking_index,listing_status,whitelist,avg_amount)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (ts_code, symbol, name, body.etf_type,
                         body.tracking_index, listing_status,
                         bool(body.whitelist), body.avg_amount),
                    )
                finally:
                    cur.close()
                created = True
            else:
                values = body.dict(exclude_unset=True)
                values.pop("ts_code", None)
                updates = _prepare_update_values(values)
                if updates:
                    assignments = ", ".join(f"{field}=%s" for field in updates)
                    cur = conn.cursor()
                    try:
                        cur.execute(
                            f"UPDATE market_etf_basic SET {assignments} WHERE ts_code=%s",
                            tuple(updates.values()) + (ts_code,),
                        )
                    finally:
                        cur.close()
                created = False
            status = _status_from_conn(conn, ts_code)
    except APIError:
        raise
    except Exception as exc:
        raise _storage_error() from exc
    return {"created": created, "etf": status}


@router.patch("/{ts_code}")
def update_etf(ts_code: str, body: UpdateEtfRequest, request: Request,
               x_api_key: Optional[str] = Header(None)):
    """更新 ETF 的白名单、上市状态或人工维护的元数据。"""
    require_trade_run_api_key(request, x_api_key)
    normalized_code = _normalize_request_code(ts_code)
    updates = _prepare_update_values(body.dict(exclude_unset=True))
    if not updates:
        raise APIError("EMPTY_ETF_UPDATE", "请至少提供一个需要更新的 ETF 字段", 422)
    try:
        with get_conn() as conn:
            if _status_from_conn(conn, normalized_code) is None:
                raise APIError("ETF_NOT_FOUND", "ETF 尚未登记；请先调用 POST /api/etfs", 404)
            assignments = ", ".join(f"{field}=%s" for field in updates)
            cur = conn.cursor()
            try:
                cur.execute(
                    f"UPDATE market_etf_basic SET {assignments} WHERE ts_code=%s",
                    tuple(updates.values()) + (normalized_code,),
                )
            finally:
                cur.close()
            status = _status_from_conn(conn, normalized_code)
    except APIError:
        raise
    except Exception as exc:
        raise _storage_error() from exc
    return {"updated": True, "etf": status}


@router.get("/{ts_code}/data-status")
def get_etf_data_status(ts_code: str, request: Request,
                        x_api_key: Optional[str] = Header(None)):
    """返回是否可进入 ETF 扫描所需的白名单和历史日线准备状态。"""
    require_trade_run_api_key(request, x_api_key)
    normalized_code = _normalize_request_code(ts_code)
    try:
        with get_conn() as conn:
            status = _status_from_conn(conn, normalized_code)
    except Exception as exc:
        raise _storage_error() from exc
    if status is None:
        return {
            "ts_code": normalized_code,
            "exists": False,
            "whitelist": False,
            "daily_count": 0,
            "data_state": "not_registered",
            "scan_ready": False,
        }
    return {"exists": True, **status}


@router.post("/{ts_code}/sync-history")
def sync_etf_history(ts_code: str, request: Request,
                     body: Optional[EtfHistorySyncRequest] = None,
                     x_api_key: Optional[str] = Header(None)):
    """异步拉取并写入单只已登记 ETF 的未复权历史日线。"""
    require_trade_run_api_key(request, x_api_key)
    normalized_code = _normalize_request_code(ts_code)
    _require_registered_etf(normalized_code)
    body = body or EtfHistorySyncRequest()
    try:
        start_date, end_date = build_history_window(body.start_date, body.end_date)
    except ValueError as exc:
        raise APIError("INVALID_HISTORY_WINDOW", str(exc), 422) from exc
    task = task_mgr.submit(
        "etf_history_sync", run_etf_history_sync, normalized_code, start_date, end_date,
        params={
            "ts_code": normalized_code,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
    )
    return {
        "task_id": task.task_id,
        "status": task.status,
        "task": task.to_dict(include_result=False),
        "tip": f"轮询 GET /api/tasks/{task.task_id} 查看日线初始化进度",
    }
