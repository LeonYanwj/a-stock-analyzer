"""股票数据查询 API"""
from datetime import date
from typing import Optional, List
from fastapi import APIRouter, HTTPException

import pandas as pd

from data.db import get_conn
from api.schemas import StockBasic, DailyBar


router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@router.get("", response_model=List[StockBasic])
def list_stocks(active_only: bool = True, limit: int = 200,
                industry: Optional[str] = None, search: Optional[str] = None):
    """股票列表"""
    sql = "SELECT ts_code, symbol, name, industry, list_date, is_active, is_st FROM market_stock_basic"
    conds = []
    params = []
    if active_only:
        conds.append("is_active=1")
    if industry:
        conds.append("industry=%s")
        params.append(industry)
    if search:
        conds.append("(symbol LIKE %s OR name LIKE %s)")
        params.append(f"%{search}%")
        params.append(f"%{search}%")
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY ts_code LIMIT %s"
    params.append(limit)
    with get_conn() as conn:
        df = pd.read_sql(sql, conn, params=tuple(params))
    if df.empty:
        return []
    return df.to_dict("records")


@router.get("/{ts_code}", response_model=StockBasic)
def get_stock(ts_code: str):
    """单股基础信息"""
    with get_conn() as conn:
        df = pd.read_sql(
            "SELECT ts_code, symbol, name, industry, list_date, is_active, is_st "
            "FROM market_stock_basic WHERE ts_code=%s",
            conn, params=(ts_code,))
    if df.empty:
        raise HTTPException(404, "股票不存在")
    return df.iloc[0].to_dict()


@router.get("/{ts_code}/daily", response_model=List[DailyBar])
def get_daily(ts_code: str, start_date: Optional[date] = None,
              end_date: Optional[date] = None, limit: int = 90):
    """单股日线（从 DB 直接查）"""
    sql = ("SELECT trade_date, open, high, low, close, vol, pct_chg "
           "FROM market_daily WHERE ts_code=%s AND adjust='qfq'")
    params = [ts_code]
    if start_date:
        sql += " AND trade_date>=%s"
        params.append(start_date)
    if end_date:
        sql += " AND trade_date<=%s"
        params.append(end_date)
    sql += " ORDER BY trade_date DESC LIMIT %s"
    params.append(limit)
    with get_conn() as conn:
        df = pd.read_sql(sql, conn, params=tuple(params))
    if df.empty:
        return []
    df = df.sort_values("trade_date")
    return df.to_dict("records")


@router.get("/{ts_code}/valuation")
def get_valuation(ts_code: str, limit: int = 30):
    """单股估值历史（PE/PB）"""
    with get_conn() as conn:
        df = pd.read_sql(
            "SELECT trade_date, pe, pe_ttm, pb, ps, total_mv FROM market_valuation "
            "WHERE ts_code=%s ORDER BY trade_date DESC LIMIT %s",
            conn, params=(ts_code, limit))
    if df.empty:
        return []
    return df.sort_values("trade_date").to_dict("records")


@router.get("/{ts_code}/financial")
def get_financial(ts_code: str, limit: int = 20):
    """单股财务历史"""
    with get_conn() as conn:
        df = pd.read_sql(
            "SELECT * FROM market_financial WHERE ts_code=%s "
            "ORDER BY report_date DESC LIMIT %s",
            conn, params=(ts_code, limit))
    if df.empty:
        return []
    return df.to_dict("records")
