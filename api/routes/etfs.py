"""ETF 白名单与流动性状态查询；不提供自动下单。"""
from typing import Optional

from fastapi import APIRouter, Header, Query
from api.errors import APIError
from api.routes.trade_runs import require_trade_run_api_key
from data.db import get_conn


router = APIRouter(prefix="/api/etfs", tags=["etfs"])


@router.get("")
def list_etfs(search: Optional[str] = None, etf_type: Optional[str] = None,
              whitelist_only: bool = True, limit: int = Query(200, ge=1, le=500),
              x_api_key: Optional[str] = Header(None)):
    require_trade_run_api_key(x_api_key)
    sql = "SELECT ts_code,symbol,name,etf_type,tracking_index,listing_status,whitelist,avg_amount,updated_at FROM market_etf_basic"
    conditions, params = ["listing_status='active'"], []
    if whitelist_only:
        conditions.append("whitelist=1")
    if etf_type:
        conditions.append("etf_type=%s")
        params.append(etf_type)
    if search:
        conditions.append("(ts_code LIKE %s OR symbol LIKE %s OR name LIKE %s OR tracking_index LIKE %s)")
        params.extend([f"%{search}%"] * 4)
    sql += " WHERE " + " AND ".join(conditions) + " ORDER BY avg_amount DESC, ts_code LIMIT %s"
    params.append(limit)
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            columns = [x[0] for x in cur.description]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]
            cur.close()
        return rows
    except Exception as exc:
        raise APIError("ETF_DATA_NOT_AVAILABLE", "ETF 基础信息尚未准备好", 503, str(exc)[:200])
