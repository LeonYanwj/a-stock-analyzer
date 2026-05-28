"""账户/持仓/成交/复盘 API"""
import json
import traceback
from datetime import date
from typing import List, Optional
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

import paper_engine as eng
from api.schemas import (AccountSummary, AccountHistoryRow, PositionRow,
                          TradeRow, EquityPoint)
from api.errors import NotFound, BadRequest
from api import tasks as task_mgr


router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("", response_model=List[AccountSummary])
def list_accounts(status: str = "active"):
    """模拟盘账户列表

    Args:
        status: active (默认，仅运行中) / terminated (仅已终止) / all (全部)
    """
    if status not in ("active", "terminated", "all"):
        raise BadRequest(f"status 取值: active/terminated/all，收到 {status}",
                         code="INVALID_STATUS")
    df = eng.list_accounts(status=status)
    if df.empty:
        return []
    return df.to_dict("records")


@router.get("/history", response_model=List[AccountHistoryRow])
def list_account_history():
    """已终止的模拟盘历史列表（含持续天数、最终收益率）"""
    df = eng.list_terminated_accounts()
    if df.empty:
        return []
    rows = df.to_dict("records")
    # 把 numpy 类型转 Python 原生
    for r in rows:
        for k in ("initial_capital", "final_equity", "final_return_pct"):
            if r.get(k) is not None:
                r[k] = float(r[k])
        if r.get("days_run") is not None:
            r["days_run"] = int(r["days_run"])
    return rows


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


def _get_realtime_prices(ts_codes: list) -> dict:
    """拉一次全市场 spot，取出指定 ts_code 的最新价。失败返回空 dict。

    cached in DataFetcher（进程级），多个账户连续查 positions 不会重复拉。
    """
    try:
        from data.fetcher import DataFetcher
        spot = DataFetcher().get_market_snapshot()
        if spot.empty:
            return {}
        sub = spot[spot["ts_code"].isin(ts_codes)][["ts_code", "close"]]
        return {row["ts_code"]: float(row["close"]) for _, row in sub.iterrows()
                if row.get("close") is not None}
    except Exception:
        return {}


def _build_position_rows(df, realtime_prices: dict, use_date) -> list:
    """从持仓 DataFrame + 实时价 dict 算每只股票的最终展示行（同步/SSE 共用）"""
    rows = []
    for _, p in df.iterrows():
        avg_cost = float(p["avg_cost"])
        ts_code = p["ts_code"]
        if ts_code in realtime_prices:
            price = realtime_prices[ts_code]
            source = "realtime"
        else:
            close = eng.get_close_price(ts_code, use_date)
            if close is not None:
                price = float(close)
                source = "close"
            else:
                price = avg_cost
                source = "cost"
        ret = price / avg_cost - 1 if avg_cost > 0 else 0
        rows.append({
            "ts_code": ts_code,
            "name": p.get("name"),
            "qty": int(p["qty"]),
            "avg_cost": avg_cost,
            "current_price": price,
            "return_pct": ret,
            "market_value": float(p["qty"]) * price,
            "open_date": p["open_date"],
            "price_source": source,
        })
    return rows


@router.get("/{account_id}/positions", response_model=List[PositionRow])
def get_positions(account_id: int, asof: Optional[date] = None,
                  use_realtime: bool = True):
    """【同步】账户当前持仓（含价格/收益率）

    价格优先级：
      - 不传 asof + use_realtime=true → 拉 spot 拿实时价（盘中是分时价，盘后是当日收盘）
      - 传 asof 或者 use_realtime=false → 用 DB 收盘价（历史日期或离线场景）
      - 实时价拉失败 → 自动回退 DB 收盘价
      - 都没有 → 用持仓成本（避免 None）

    ⚠️ use_realtime=true 时会调外网（AKShare），可能阻塞 5-30 秒。
       前端建议改用 SSE 流式版本：GET /api/accounts/{id}/positions/stream

    返回字段 `price_source`：realtime / close / cost
    """
    df = eng.get_positions(account_id)
    if df.empty:
        return []

    use_date = asof or date.today()
    want_realtime = use_realtime and asof is None
    realtime_prices = _get_realtime_prices(df["ts_code"].tolist()) if want_realtime else {}
    return _build_position_rows(df, realtime_prices, use_date)


# -------------------- SSE 流式持仓 --------------------
def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False, default=str)}\n\n"


def _positions_stream_gen(account_id: int, asof, use_realtime: bool):
    """生成器：分阶段推送持仓查询进度

    事件:
      - {"progress": int, "msg": str}            进度
      - {"progress": 100, "result": [...]}       最终持仓列表（同同步版 schema）
      - {"error": str, "message": str}           失败
    """
    try:
        yield _sse({"progress": 5, "msg": f"查账户 {account_id} 是否存在..."})
        if eng.get_account(account_id) is None:
            yield _sse({"error": "ACCOUNT_NOT_FOUND",
                        "message": f"账户 {account_id} 不存在"})
            return

        yield _sse({"progress": 15, "msg": "查持仓明细（含股票名称）..."})
        df = eng.get_positions(account_id)
        if df.empty:
            yield _sse({"progress": 100, "result": []})
            return

        use_date = asof or date.today()
        want_realtime = use_realtime and asof is None

        realtime_prices = {}
        if want_realtime:
            yield _sse({"progress": 30,
                        "msg": f"拉 AKShare 实时价（{len(df)} 只股票，外网可能慢）..."})
            realtime_prices = _get_realtime_prices(df["ts_code"].tolist())
            hit = sum(1 for tc in df["ts_code"] if tc in realtime_prices)
            yield _sse({"progress": 80,
                        "msg": f"实时价拿到 {hit}/{len(df)} 只（其余降级 DB 收盘价）"})
        else:
            yield _sse({"progress": 50, "msg": "使用 DB 收盘价（未启用实时）..."})

        yield _sse({"progress": 90, "msg": "计算收益率和市值..."})
        rows = _build_position_rows(df, realtime_prices, use_date)

        yield _sse({"progress": 100, "result": rows})

    except Exception as e:
        yield _sse({
            "error": type(e).__name__,
            "message": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc()[-500:],
        })


@router.get("/{account_id}/positions/stream")
def get_positions_stream(account_id: int, asof: Optional[date] = None,
                         use_realtime: bool = True):
    """【SSE 流式】持仓查询 - 实时推送进度（外网调用专用）

    跟同步版 /positions 返回同样的数据，但分 5 个阶段推送：
      data: {"progress": 15, "msg": "查持仓明细..."}
      data: {"progress": 30, "msg": "拉 AKShare 实时价..."}     ← 主要慢点
      data: {"progress": 80, "msg": "实时价拿到 5/5 只..."}
      data: {"progress": 90, "msg": "计算收益率..."}
      data: {"progress": 100, "result": [...持仓数组...]}

    命令行测试：
      curl -N "http://localhost:8000/api/accounts/1/positions/stream"

    前端 JS（参考 /api/rate/{code}/stream 用法相同）：
      const ev = new EventSource('/api/accounts/1/positions/stream');
      ev.onmessage = e => {
        const d = JSON.parse(e.data);
        if (d.result)  { showTable(d.result); ev.close(); }
        if (d.error)   { showError(d.message); ev.close(); }
        if (d.progress != null) updateBar(d.progress, d.msg);
      };
    """
    return StreamingResponse(
        _positions_stream_gen(account_id, asof, use_realtime),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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

    params = {"account_id": account_id, "limit": limit,
              "asof": str(asof) if asof else None, "enable_news": enable_news}
    task = task_mgr.submit(
        "auto_rebalance", _do_auto_rebalance, params=params,
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

    params = {"account_id": account_id, "asof": str(asof) if asof else None,
              "limit": limit, "dry_run": dry_run}
    task = task_mgr.submit(
        "daily_run", _do_daily_run, params=params,
        account_id=account_id, asof=asof, limit=limit, dry_run=dry_run,
    )
    return {"task_id": task.task_id, "status": task.status,
            "tip": f"轮询 GET /api/tasks/{task.task_id}"}


# -------------------- 终止模拟盘（清仓 + 归档） --------------------
def _do_terminate(task, account_id: int, use_realtime: bool):
    """异步任务：卖光持仓 + 归档 final_equity / final_return_pct + is_active=0

    步骤:
      1. 查持仓
      2. (可选) 拉 AKShare spot 实时价
      3. 卖光全部持仓
      4. 写 paper_account 归档字段
    """
    task.report(5, "查账户...")
    acc = eng.get_account(account_id)
    if acc is None:
        raise NotFound(f"账户 {account_id} 不存在", code="ACCOUNT_NOT_FOUND")
    if int(acc.get("is_active", 0)) == 0:
        raise BadRequest(f"账户 {account_id} 已终止，不能重复终止",
                         code="ACCOUNT_ALREADY_TERMINATED")

    task.report(15, "查当前持仓...")
    df = eng.get_positions(account_id)

    price_override = None
    if use_realtime and not df.empty:
        task.report(40, f"拉 AKShare 实时价（{len(df)} 只持仓，外网可能慢）...")
        price_override = _get_realtime_prices(df["ts_code"].tolist())
        task.report(70, f"实时价 {len(price_override)}/{len(df)} 只，开始卖出...")
    else:
        task.report(50, "无持仓需要卖出..." if df.empty else "用 DB 收盘价卖出...")

    task.report(80, "执行清仓 + 归档...")
    result = eng.terminate_account(account_id, price_override=price_override)

    task.report(100, f"已终止，最终权益 {result['final_equity']:.2f} "
                     f"({result['final_return_pct']*100:+.2f}%)")
    return result


@router.post("/{account_id}/terminate/async")
def terminate_account_async(account_id: int, use_realtime: bool = True):
    """【异步】终止模拟盘：卖光全部持仓 + 归档最终权益和累计收益率

    生命周期：is_active=1 → 调用本接口 → is_active=0（不可恢复）
    终止后账户进入"历史归档"，可通过 GET /api/accounts/history 列出。

    Args:
        use_realtime: 用 AKShare 实时价卖出（默认 true，盘后自动用收盘价）

    返回 task_id，轮询 GET /api/tasks/{task_id}
    结果字段：n_sold/total_revenue/final_cash/final_equity/final_return_pct/ended_at
    """
    acc = eng.get_account(account_id)
    if acc is None:
        raise NotFound(f"账户 {account_id} 不存在", code="ACCOUNT_NOT_FOUND")
    if int(acc.get("is_active", 0)) == 0:
        raise BadRequest(f"账户 {account_id} 已终止（ended_at={acc.get('ended_at')}）",
                         code="ACCOUNT_ALREADY_TERMINATED")

    params = {"account_id": account_id, "use_realtime": use_realtime}
    task = task_mgr.submit(
        "terminate", _do_terminate, params=params,
        account_id=account_id, use_realtime=use_realtime,
    )
    return {"task_id": task.task_id, "status": task.status,
            "tip": f"轮询 GET /api/tasks/{task.task_id}"}
