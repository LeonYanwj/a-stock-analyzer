"""账户/持仓/成交/复盘 API"""
from datetime import date
from typing import List, Optional
from fastapi import APIRouter

import paper_engine as eng
from api.schemas import (AccountSummary, PositionRow, TradeRow, EquityPoint)
from api.errors import NotFound, BadRequest
from api import tasks as task_mgr


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
        raise BadRequest(str(e), code="INVALID_ACCOUNT_PARAMS")


@router.get("/{account_id}")
def get_account(account_id: int):
    """账户详情（含策略和参数）"""
    acc = eng.get_account(account_id)
    if acc is None:
        raise NotFound(f"账户 {account_id} 不存在", code="ACCOUNT_NOT_FOUND")
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


# -------------------- 异步任务接口 --------------------
def _do_auto_rebalance(task, account_id: int, limit: int, asof, enable_news: bool):
    """异步任务：跑 screen 选股 + 清仓 + 等权买入 + 快照

    对应 paper.py cmd_auto_rebalance 的逻辑
    """
    from screen import screen_market

    task.report(5, "查账户...")
    account = eng.get_account(account_id)
    if account is None:
        raise NotFound(f"账户 {account_id} 不存在", code="ACCOUNT_NOT_FOUND")

    task.report(10, f"跑 {account['strategy_name']} 选股...")
    picks_df = screen_market(
        strategy=account["strategy_name"],
        capital=float(account["current_equity"]),
        limit=limit,
        enable_news=enable_news,
        verbose=False,
    )
    if picks_df.empty:
        task.report(100, "选股结果为空，跳过调仓")
        return {
            "account_id": account_id,
            "skipped": True,
            "reason": "screen 返回空，未执行调仓",
        }
    picks = picks_df.index.tolist()

    task.report(70, f"清仓 + 买入 {len(picks)} 只...")
    sold = eng.sell_all(account_id, asof, reason="REBALANCE")
    bought = eng.buy_equal_weight(account_id, picks, asof, reason="REBALANCE")

    task.report(95, "保存权益快照...")
    total_equity = eng.save_equity_snapshot(account_id, asof)

    return {
        "account_id": account_id,
        "asof": str(asof) if asof else None,
        "strategy": account["strategy_name"],
        "sold": {"n": sold["n_sold"], "revenue": sold["total_revenue"]},
        "bought": {
            "n": bought["n_bought"],
            "spent": bought["total_spent"],
            "skipped": [{"ts_code": tc, "reason": why} for tc, why in bought.get("skipped", [])],
        },
        "picks": picks,
        "total_equity": float(total_equity),
    }


@router.post("/{account_id}/auto-rebalance/async")
def auto_rebalance_async(
    account_id: int,
    limit: int = 500,
    asof: Optional[date] = None,
    enable_news: bool = False,
):
    """【异步】自动调仓：跑 screen 选股 + 清仓 + 等权买入 + 保存快照

    流程：
      1. POST /api/accounts/{id}/auto-rebalance/async?limit=500
         → 返回 {"task_id": "xxx"}
      2. GET /api/tasks/{task_id}
         → 轮询直到 status=done，result 含调仓结果
    """
    acc = eng.get_account(account_id)
    if acc is None:
        raise NotFound(f"账户 {account_id} 不存在", code="ACCOUNT_NOT_FOUND")

    task = task_mgr.submit(
        "auto_rebalance", _do_auto_rebalance,
        account_id=account_id, limit=limit, asof=asof, enable_news=enable_news,
    )
    return {"task_id": task.task_id, "status": task.status,
            "tip": f"轮询 GET /api/tasks/{task.task_id}"}


def _do_daily_run(task, account_id: int, asof, limit: int, dry_run: bool):
    """异步任务：单账户跑 daily_runner 流程（止损 + 调仓 + 快照 + 报告）

    对应 daily_runner.run_one_account
    """
    from daily_runner import run_one_account
    import io
    import contextlib

    task.report(5, "查账户...")
    acc = eng.get_account(account_id)
    if acc is None:
        raise NotFound(f"账户 {account_id} 不存在", code="ACCOUNT_NOT_FOUND")

    task.report(15, "跑止损 + 调仓 + 快照 + 报告...")
    # daily_runner 是命令行函数，会 print 大量输出，捕获下来
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        trade_date = asof or date.today()
        run_one_account(acc, trade_date, limit=limit, dry_run=dry_run)
    log_text = buf.getvalue()

    task.report(95, "整理结果...")
    return {
        "account_id": account_id,
        "asof": str(asof) if asof else str(date.today()),
        "dry_run": dry_run,
        "log": log_text,
    }


@router.post("/{account_id}/daily-run/async")
def daily_run_async(
    account_id: int,
    asof: Optional[date] = None,
    limit: int = 500,
    dry_run: bool = False,
):
    """【异步】跑单账户的每日流程：检查止损 → 判断调仓日 → 调仓 → 快照 → 复盘

    等价于命令行：
        python daily_runner.py --account {id} --date YYYYMMDD

    返回 task_id，轮询 GET /api/tasks/{task_id} 查进度与结果
    """
    acc = eng.get_account(account_id)
    if acc is None:
        raise NotFound(f"账户 {account_id} 不存在", code="ACCOUNT_NOT_FOUND")

    task = task_mgr.submit(
        "daily_run", _do_daily_run,
        account_id=account_id, asof=asof, limit=limit, dry_run=dry_run,
    )
    return {"task_id": task.task_id, "status": task.status,
            "tip": f"轮询 GET /api/tasks/{task.task_id}"}
