"""账户/持仓/成交/复盘 API"""
from datetime import date
from typing import List, Optional
from fastapi import APIRouter, HTTPException

import paper_engine as eng
from api.schemas import (AccountSummary, PositionRow, TradeRow, EquityPoint)


router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("", response_model=List[AccountSummary])
def list_accounts():
    """所有模拟账户列表"""
    df = eng.list_accounts()
    if df.empty:
        return []
    return df.to_dict("records")


@router.post("", response_model=dict)
def create_account(name: str, capital: float, strategy: str):
    """创建账户"""
    try:
        aid = eng.create_account(name, capital, strategy)
        return {"account_id": aid, "name": name, "capital": capital, "strategy": strategy}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{account_id}")
def get_account(account_id: int):
    """账户详情（含策略和参数）"""
    acc = eng.get_account(account_id)
    if acc is None:
        raise HTTPException(404, "账户不存在")
    # 把 datetime/decimal 转 JSON 友好类型
    return {k: (str(v) if hasattr(v, "isoformat") else float(v) if hasattr(v, "real") and not isinstance(v, (int, bool)) else v)
            for k, v in acc.items()}


@router.get("/{account_id}/positions", response_model=List[PositionRow])
def get_positions(account_id: int, asof: Optional[date] = None):
    """账户当前持仓（含当日收盘价/收益率）"""
    df = eng.get_positions(account_id)
    if df.empty:
        return []
    use_date = asof or date.today()
    rows = []
    for _, p in df.iterrows():
        price = eng.get_close_price(p["ts_code"], use_date) or float(p["avg_cost"])
        ret = price / float(p["avg_cost"]) - 1 if float(p["avg_cost"]) > 0 else 0
        rows.append({
            "ts_code": p["ts_code"],
            "qty": int(p["qty"]),
            "avg_cost": float(p["avg_cost"]),
            "current_price": price,
            "return_pct": ret,
            "market_value": float(p["qty"]) * price,
            "open_date": p["open_date"],
        })
    return rows


@router.get("/{account_id}/trades", response_model=List[TradeRow])
def get_trades(account_id: int, limit: int = 50):
    """成交记录"""
    df = eng.get_trades(account_id, limit=limit)
    if df.empty:
        return []
    return df.to_dict("records")


@router.get("/{account_id}/equity", response_model=List[EquityPoint])
def get_equity_curve(account_id: int):
    """每日净值曲线"""
    df = eng.get_equity_curve(account_id)
    if df.empty:
        return []
    return df.to_dict("records")


@router.get("/{account_id}/report")
def get_daily_report(account_id: int, asof: Optional[date] = None):
    """生成单日复盘报告（文本）"""
    text = eng.daily_report(account_id, trade_date=asof)
    return {"report": text}


@router.post("/{account_id}/stoploss")
def trigger_stoploss(account_id: int, asof: Optional[date] = None):
    """触发止损检查"""
    r = eng.check_stoploss(account_id, asof or date.today())
    return r


@router.post("/{account_id}/snapshot")
def save_snapshot(account_id: int, asof: Optional[date] = None):
    """保存权益快照"""
    total = eng.save_equity_snapshot(account_id, asof or date.today())
    return {"total_equity": total}
