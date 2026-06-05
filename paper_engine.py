"""模拟盘引擎（Phase 2 MVP）

核心业务：账户管理 / 模拟成交 / 持仓跟踪 / 止损 / 每日权益快照

成本模型（A 股精确版）：
- 佣金:  max(成交额 × 0.0001, 5) 双边
- 印花税: 成交额 × 0.001 (仅卖出)
- 滑点:  价格 × 0.001 (买入加，卖出减)

业务规则：
- T+1 简化处理：调仓日卖买当天完成（不严格 T+1）
- 等权持仓：每只 capital / N，按 1 手 (100 股) 取整
- 止损线 -8%（持仓成本对比当日收盘价）
"""
from datetime import date, datetime
import pandas as pd

from data.db import get_conn


# -------------------- 常量 --------------------
COMMISSION_RATE = 0.0001
COMMISSION_MIN  = 5.0
STAMP_TAX_RATE  = 0.001
SLIPPAGE_RATE   = 0.001
STOPLOSS        = -0.08


# -------------------- 工具 --------------------
def _norm_date(d):
    if d is None:
        return date.today()
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, str):
        s = d.replace("-", "")
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    raise ValueError(f"无法解析日期: {d}")


def calc_commission(amount: float) -> float:
    return max(amount * COMMISSION_RATE, COMMISSION_MIN)


def calc_buy_cost(price: float, qty: int) -> tuple:
    """买入实际成本 (含佣金 + 滑点)。Returns: (total_cost, commission, actual_price)"""
    actual_price = price * (1 + SLIPPAGE_RATE)
    amount = actual_price * qty
    commission = calc_commission(amount)
    return amount + commission, commission, actual_price


def calc_sell_revenue(price: float, qty: int) -> tuple:
    """卖出实际收入。Returns: (net_revenue, total_fees, actual_price)"""
    actual_price = price * (1 - SLIPPAGE_RATE)
    amount = actual_price * qty
    commission = calc_commission(amount)
    stamp = amount * STAMP_TAX_RATE
    return amount - commission - stamp, commission + stamp, actual_price


# -------------------- 账户 --------------------
def create_account(name: str, capital: float, strategy_name: str) -> int:
    """创建模拟账户"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT strategy_id FROM strategy_config WHERE strategy_name=%s",
                   (strategy_name,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"未知策略: {strategy_name}")
        sid = row[0]
        try:
            cur.execute(
                "INSERT INTO paper_account (account_name, strategy_id, initial_capital, "
                "current_cash, current_equity) VALUES (%s, %s, %s, %s, %s)",
                (name, sid, capital, capital, capital))
        except Exception as e:
            cur.close()
            raise
        aid = cur.lastrowid
        cur.close()
        return aid


def get_account(account_id: int) -> dict:
    with get_conn() as conn:
        df = pd.read_sql(
            "SELECT a.*, s.strategy_name, s.rebal_weeks "
            "FROM paper_account a JOIN strategy_config s USING(strategy_id) "
            "WHERE a.account_id=%s", conn, params=(account_id,))
        if df.empty:
            return None
        return df.iloc[0].to_dict()


def list_accounts(status: str = "all") -> pd.DataFrame:
    """列出账户

    Args:
        status: 'active' 仅运行中 / 'terminated' 仅已终止 / 'all' 全部
    """
    sql = ("SELECT a.account_id, a.account_name, s.strategy_name, "
           "a.initial_capital, a.current_cash, a.current_equity, "
           "ROUND((a.current_equity - a.initial_capital) / a.initial_capital * 100, 2) AS return_pct, "
           "a.started_at, a.is_active, a.ended_at "
           "FROM paper_account a JOIN strategy_config s USING(strategy_id) ")
    if status == "active":
        sql += "WHERE a.is_active=1 "
    elif status == "terminated":
        sql += "WHERE a.is_active=0 "
    sql += "ORDER BY a.started_at DESC"
    with get_conn() as conn:
        return pd.read_sql(sql, conn)


# -------------------- 价格查询 --------------------
def get_close_price(ts_code: str, trade_date: date) -> float:
    """查某日及之前最近的收盘价（含当日停牌兼容）"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT close FROM market_daily WHERE ts_code=%s AND adjust='qfq' "
            "AND trade_date<=%s ORDER BY trade_date DESC LIMIT 1",
            (ts_code, trade_date))
        row = cur.fetchone()
        cur.close()
        return float(row[0]) if row and row[0] else None


def get_ma(ts_code: str, trade_date, n: int = 5) -> float:
    """N 日均线：trade_date 之前最近 N 个交易日收盘均值。

    盘中调用时今日尚未收盘，故只用已确定的历史收盘价（trade_date<当天）。
    不足 N 条返回 None（不足以判断趋势，宁可不触发）。
    """
    trade_date = _norm_date(trade_date)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT close FROM market_daily WHERE ts_code=%s AND adjust='qfq' "
            "AND trade_date<%s ORDER BY trade_date DESC LIMIT %s",
            (ts_code, trade_date, n))
        rows = cur.fetchall()
        cur.close()
    closes = [float(r[0]) for r in rows if r and r[0]]
    if len(closes) < n:
        return None
    return sum(closes) / len(closes)


def get_realtime_prices(ts_codes: list) -> dict:
    """拉一次全市场 spot 快照，取出指定股票的实时价。

    盘中=分时现价，盘后=当日收盘。失败返回空 dict（调用方自行降级/跳过）。
    """
    if not ts_codes:
        return {}
    try:
        from data.fetcher import DataFetcher
        spot = DataFetcher().get_market_snapshot()
        if spot.empty:
            return {}
        sub = spot[spot["ts_code"].isin(ts_codes)][["ts_code", "close"]]
        return {r["ts_code"]: float(r["close"]) for _, r in sub.iterrows()
                if r["close"] and float(r["close"]) > 0}
    except Exception:
        return {}


# -------------------- 持仓 --------------------
def get_positions(account_id: int) -> pd.DataFrame:
    """持仓明细。JOIN market_stock_basic 带出股票名称。"""
    with get_conn() as conn:
        return pd.read_sql(
            "SELECT p.account_id, p.ts_code, b.name, p.qty, p.avg_cost, "
            "p.open_date, p.last_update "
            "FROM paper_position p "
            "LEFT JOIN market_stock_basic b ON b.ts_code = p.ts_code "
            "WHERE p.account_id=%s ORDER BY p.ts_code",
            conn, params=(account_id,))


# -------------------- 成交 --------------------
def execute_buy(account_id: int, ts_code: str, qty: int, price: float,
                trade_date: date, reason: str = "SIGNAL") -> dict:
    """执行买入"""
    cost, comm, actual_price = calc_buy_cost(price, qty)
    trade_date = _norm_date(trade_date)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE paper_account SET current_cash=current_cash-%s WHERE account_id=%s",
                   (cost, account_id))
        cur.execute(
            "INSERT INTO paper_order (account_id, ts_code, side, order_type, qty, price, "
            "status, reason, filled_at) VALUES (%s,%s,'BUY','MARKET',%s,%s,'FILLED',%s,NOW())",
            (account_id, ts_code, qty, actual_price, reason))
        oid = cur.lastrowid
        cur.execute(
            "INSERT INTO paper_trade (order_id, account_id, ts_code, side, price, qty, "
            "amount, commission, trade_date, trade_time, reason) "
            "VALUES (%s,%s,%s,'BUY',%s,%s,%s,%s,%s,%s,%s)",
            (oid, account_id, ts_code, actual_price, qty, actual_price*qty, comm,
             trade_date, datetime.combine(trade_date, datetime.min.time()).replace(hour=15), reason))
        # upsert position
        cur.execute("SELECT qty, avg_cost FROM paper_position WHERE account_id=%s AND ts_code=%s",
                   (account_id, ts_code))
        ex = cur.fetchone()
        if ex:
            old_qty, old_avg = ex[0], float(ex[1])
            new_qty = old_qty + qty
            new_avg = (old_qty*old_avg + qty*actual_price) / new_qty
            cur.execute(
                "UPDATE paper_position SET qty=%s, avg_cost=%s WHERE account_id=%s AND ts_code=%s",
                (new_qty, new_avg, account_id, ts_code))
        else:
            cur.execute(
                "INSERT INTO paper_position (account_id, ts_code, qty, avg_cost, open_date) "
                "VALUES (%s,%s,%s,%s,%s)",
                (account_id, ts_code, qty, actual_price, trade_date))
        cur.close()
    return {"order_id": oid, "cost": cost, "price": actual_price, "commission": comm}


def execute_sell(account_id: int, ts_code: str, qty: int, price: float,
                 trade_date: date, reason: str = "SIGNAL") -> dict:
    """执行卖出"""
    revenue, fees, actual_price = calc_sell_revenue(price, qty)
    trade_date = _norm_date(trade_date)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE paper_account SET current_cash=current_cash+%s WHERE account_id=%s",
                   (revenue, account_id))
        cur.execute(
            "INSERT INTO paper_order (account_id, ts_code, side, order_type, qty, price, "
            "status, reason, filled_at) VALUES (%s,%s,'SELL','MARKET',%s,%s,'FILLED',%s,NOW())",
            (account_id, ts_code, qty, actual_price, reason))
        oid = cur.lastrowid
        cur.execute(
            "INSERT INTO paper_trade (order_id, account_id, ts_code, side, price, qty, "
            "amount, commission, trade_date, trade_time, reason) "
            "VALUES (%s,%s,%s,'SELL',%s,%s,%s,%s,%s,%s,%s)",
            (oid, account_id, ts_code, actual_price, qty, actual_price*qty, fees,
             trade_date, datetime.combine(trade_date, datetime.min.time()).replace(hour=15), reason))
        cur.execute("SELECT qty FROM paper_position WHERE account_id=%s AND ts_code=%s",
                   (account_id, ts_code))
        ex = cur.fetchone()
        if ex:
            new_qty = ex[0] - qty
            if new_qty <= 0:
                cur.execute("DELETE FROM paper_position WHERE account_id=%s AND ts_code=%s",
                           (account_id, ts_code))
            else:
                cur.execute("UPDATE paper_position SET qty=%s WHERE account_id=%s AND ts_code=%s",
                           (new_qty, account_id, ts_code))
        cur.close()
    return {"order_id": oid, "revenue": revenue, "price": actual_price, "fees": fees}


def sell_all(account_id: int, trade_date, reason: str = "REBALANCE",
             price_override: dict = None) -> dict:
    """清仓

    Args:
        price_override: {ts_code: price} 用指定价格成交，没传则用 DB 收盘价
    """
    positions = get_positions(account_id)
    if positions.empty:
        return {"n_sold": 0, "total_revenue": 0, "skipped": []}
    trade_date = _norm_date(trade_date)
    price_override = price_override or {}
    total_rev = 0
    n = 0
    skipped = []
    for _, p in positions.iterrows():
        tc = p["ts_code"]
        price = price_override.get(tc) or get_close_price(tc, trade_date)
        if price is None:
            skipped.append({"ts_code": tc, "reason": "no_price"})
            continue
        r = execute_sell(account_id, tc, int(p["qty"]), price, trade_date, reason)
        total_rev += r["revenue"]
        n += 1
    return {"n_sold": n, "total_revenue": total_rev, "skipped": skipped}


def terminate_account(account_id: int, trade_date=None,
                      price_override: dict = None) -> dict:
    """终止账户：卖光所有持仓 + 归档最终权益 + 设 is_active=0

    Args:
        trade_date: 终止日（默认今天）
        price_override: {ts_code: realtime_price} 用实时价卖出，没传则用 DB 收盘价

    Returns:
        { account_id, n_sold, total_revenue, final_cash, final_equity,
          final_return_pct, ended_at, skipped }
    """
    acc = get_account(account_id)
    if acc is None:
        raise ValueError(f"账户 {account_id} 不存在")
    if int(acc.get("is_active", 0)) == 0:
        raise ValueError(f"账户 {account_id} 已经终止过，不能重复终止")

    trade_date = _norm_date(trade_date or date.today())
    initial_capital = float(acc["initial_capital"])

    # 1. 卖光（带实时价 override）
    sell_result = sell_all(account_id, trade_date, reason="TERMINATE",
                           price_override=price_override)

    # 2. 重新读账户拿到 sell 后的现金
    acc_after = get_account(account_id)
    final_cash = float(acc_after["current_cash"])
    # 卖光后理论上没持仓了，但 skipped 的可能还有市值
    _, mv, final_equity = calc_total_equity(account_id, trade_date)

    # 3. 累计收益率
    if initial_capital > 0:
        final_return_pct = (final_equity - initial_capital) / initial_capital
    else:
        final_return_pct = 0.0

    # 4. 标记账户终止
    ended_at = datetime.now()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE paper_account SET is_active=0, ended_at=%s, "
            "final_equity=%s, final_return_pct=%s, current_equity=%s "
            "WHERE account_id=%s",
            (ended_at, final_equity, final_return_pct, final_equity, account_id))
        cur.close()

    # 5. 顺便存一份当日权益快照（用于历史回看）
    try:
        save_equity_snapshot(account_id, trade_date)
    except Exception:
        pass

    return {
        "account_id": account_id,
        "ended_at": ended_at.isoformat(),
        "n_sold": sell_result["n_sold"],
        "total_revenue": sell_result["total_revenue"],
        "skipped": sell_result.get("skipped", []),
        "final_cash": final_cash,
        "remaining_market_value": float(mv),
        "final_equity": float(final_equity),
        "initial_capital": initial_capital,
        "final_return_pct": float(final_return_pct),
    }


def list_terminated_accounts() -> pd.DataFrame:
    """列出所有已终止账户，含持续天数、累计收益率（用于历史归档展示）"""
    with get_conn() as conn:
        return pd.read_sql(
            "SELECT a.account_id, a.account_name, s.strategy_name, "
            "a.initial_capital, a.final_equity, a.final_return_pct, "
            "a.started_at, a.ended_at, "
            "DATEDIFF(a.ended_at, a.started_at) AS days_run, "
            "a.note "
            "FROM paper_account a "
            "LEFT JOIN strategy_config s USING(strategy_id) "
            "WHERE a.is_active=0 "
            "ORDER BY a.ended_at DESC",
            conn)


def delete_account(account_id: int) -> dict:
    """彻底删除账户（仅限已终止账户），连带清除其持仓/成交/权益/订单历史。

    安全保护：运行中账户拒绝删除（返回 reason=STILL_ACTIVE，由调用方转 400）。
    用单事务删除多表，保证要么全删要么全不删（不留孤儿数据）。

    Returns: {deleted: bool, account_id, reason?, removed: {表名: 删除行数}}
    """
    acc = get_account(account_id)
    if acc is None:
        return {"deleted": False, "reason": "NOT_FOUND", "account_id": account_id}
    if int(acc.get("is_active", 0)) == 1:
        return {"deleted": False, "reason": "STILL_ACTIVE", "account_id": account_id}

    removed = {}
    conn = get_conn(autocommit=False)
    try:
        cur = conn.cursor()
        # 先删子表再删主表，避免外键约束
        for tbl in ("paper_position", "paper_trade", "paper_equity_daily", "paper_order"):
            cur.execute(f"DELETE FROM {tbl} WHERE account_id=%s", (account_id,))
            removed[tbl] = cur.rowcount
        cur.execute("DELETE FROM paper_account WHERE account_id=%s", (account_id,))
        removed["paper_account"] = cur.rowcount
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"deleted": True, "account_id": account_id, "removed": removed}


def buy_equal_weight(account_id: int, picks: list, trade_date,
                     reason: str = "REBALANCE") -> dict:
    """按等权买入选股列表（每只 cash/N，1 手整数）"""
    if not picks:
        return {"n_bought": 0, "total_spent": 0}
    trade_date = _norm_date(trade_date)
    acc = get_account(account_id)
    cash = float(acc["current_cash"])
    per_pos = cash / len(picks)
    total = 0
    n = 0
    skipped = []
    for tc in picks:
        price = get_close_price(tc, trade_date)
        if price is None:
            skipped.append((tc, "无当日价"))
            continue
        max_qty = int(per_pos / (price * (1 + SLIPPAGE_RATE)) / 100) * 100
        if max_qty < 100:
            skipped.append((tc, f"单股仓位{per_pos:.0f} < 1 手"))
            continue
        r = execute_buy(account_id, tc, max_qty, price, trade_date, reason)
        total += r["cost"]
        n += 1
    return {"n_bought": n, "total_spent": total, "skipped": skipped}


# -------------------- 调仓日判断 --------------------
def is_rebal_day(account_id: int, trade_date) -> bool:
    """判断今日是否调仓日

    规则: 从最后一次 REBALANCE 交易日算起，超过 rebal_weeks 周即为调仓日。
    从未调过仓的账户首日就是调仓日。
    """
    trade_date = _norm_date(trade_date)
    account = get_account(account_id)
    if account is None:
        return False
    rebal_weeks = int(account.get("rebal_weeks", 2))
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT MAX(trade_date) FROM paper_trade "
            "WHERE account_id=%s AND reason='REBALANCE'",
            (account_id,))
        row = cur.fetchone()
        cur.close()
    last = row[0] if row else None
    if last is None:
        return True
    last = _norm_date(last)
    return (trade_date - last).days >= rebal_weeks * 7


# -------------------- 信号驱动·增量换仓 --------------------
def rebalance_by_signal(account_id: int, trade_date, top_n_target: int = None,
                        limit: int = 500, keep_buffer: float = None,
                        min_score: float = 0.0) -> dict:
    """信号驱动·增量换仓（短线/波段策略每个交易日跑）

    与周期调仓（清仓重买）不同，这里只动该动的：
      1. 重算全市场因子打分，取前 top_n*keep_buffer 只为「保留宽限带」
      2. 持仓股仍在宽限带 → 继续持有（不动，省手续费、保住强势趋势）
      3. 持仓股跌出宽限带 → 视为转弱信号，卖出
      4. 空出的仓位 → 从当日最强 top_n 里补入当前没持有的新晋强势股

    宽限带 > top_n 是为了避免持仓股在第 N 名上下抖动导致来回买卖。

    Returns:
        {held_before, kept, sold, bought, top_n, keep_depth,
         sell_detail:[...], buy_detail:[...]}  或 {error:...}
    """
    from screen import screen_market
    from strategies import calc_optimal_top_n, get_keep_buffer

    trade_date = _norm_date(trade_date)
    acc = get_account(account_id)
    if acc is None:
        return {"error": "account_not_found"}

    strategy = acc["strategy_name"]
    equity = float(acc["current_equity"])
    if top_n_target is None:
        top_n_target = calc_optimal_top_n(equity)
    if keep_buffer is None:
        keep_buffer = get_keep_buffer(strategy)

    # 拉到「宽限带」深度的排名（已按分降序）
    keep_depth = max(top_n_target + 1, int(round(top_n_target * keep_buffer)))
    picks_df = screen_market(strategy=strategy, capital=equity,
                             top_n_arg=keep_depth, limit=limit, verbose=False)
    if picks_df.empty:
        return {"error": "screen_empty", "sold": 0, "bought": 0, "kept": 0}

    ranked = picks_df.index.tolist()
    buy_pool = ranked[:top_n_target]     # 最强 N 只：买入候选
    keep_set = set(ranked)               # 宽限带：保留判断

    pos = get_positions(account_id)
    held = pos["ts_code"].tolist() if not pos.empty else []

    # 1. 卖出跌出宽限带的持仓（转弱信号）
    sold, sell_detail = 0, []
    if not pos.empty:
        for _, p in pos.iterrows():
            tc = p["ts_code"]
            if tc in keep_set:
                continue
            price = get_close_price(tc, trade_date)
            if price is None:
                continue
            execute_sell(account_id, tc, int(p["qty"]), price, trade_date,
                         reason="SIGNAL")
            sold += 1
            sell_detail.append(tc)

    # 2. 保留的持仓 + 还空几个仓位
    held_after = [tc for tc in held if tc in keep_set]
    slots = max(0, top_n_target - len(held_after))

    # 3. 买入新晋强势股补满空位（等权，单股目标 = 总权益/top_n，受现金约束）
    #    质量门槛：打分须 > min_score(默认0，即强于横截面均值)才买，否则宁可空仓
    scores = picks_df["score"] if "score" in picks_df.columns else None

    def _good(tc):
        if scores is None:
            return True
        s = scores.get(tc)
        return s is not None and pd.notna(s) and s > min_score

    to_buy = [tc for tc in buy_pool if tc not in held_after and _good(tc)][:slots]
    bought, buy_detail = 0, []
    if to_buy:
        cash = float(get_account(account_id)["current_cash"])
        per_pos = min(equity / top_n_target, cash / len(to_buy))
        for tc in to_buy:
            price = get_close_price(tc, trade_date)
            if price is None:
                continue
            qty = int(per_pos / (price * (1 + SLIPPAGE_RATE)) / 100) * 100
            if qty < 100:
                continue
            execute_buy(account_id, tc, qty, price, trade_date, reason="SIGNAL")
            bought += 1
            buy_detail.append(tc)

    return {
        "held_before": len(held), "kept": len(held_after),
        "sold": sold, "bought": bought,
        "top_n": top_n_target, "keep_depth": keep_depth,
        "sell_detail": sell_detail, "buy_detail": buy_detail,
    }


# -------------------- 止损 --------------------
def check_stoploss(account_id: int, trade_date) -> dict:
    """检查持仓收益是否触发止损"""
    trade_date = _norm_date(trade_date)
    positions = get_positions(account_id)
    if positions.empty:
        return {"triggered": 0, "details": []}
    triggered = []
    for _, p in positions.iterrows():
        price = get_close_price(p["ts_code"], trade_date)
        if price is None:
            continue
        ret = price / float(p["avg_cost"]) - 1
        if ret <= STOPLOSS:
            execute_sell(account_id, p["ts_code"], int(p["qty"]),
                        price, trade_date, reason="STOPLOSS")
            triggered.append({"ts_code": p["ts_code"], "ret": ret, "price": price})
    return {"triggered": len(triggered), "details": triggered}


# -------------------- 盘中实时监控（短线） --------------------
def intraday_monitor(account_id: int, trade_date=None) -> dict:
    """盘中实时监控持仓（短线每 N 分钟跑一次）。

    只评估持仓、只卖不买（买入放收盘扫描）：
      - 实时价跌破成本 -8%        → 清仓（硬止损）
      - 实时价跌破 5 日均线(MA5)   → 清仓（短线趋势走坏）
    拿不到某股实时价则跳过该股（不在缺价时误操作）。

    Returns: {checked, sold, detail:[{ts_code, reason, price, ret}], no_price}
    """
    trade_date = _norm_date(trade_date or date.today())
    positions = get_positions(account_id)
    if positions.empty:
        return {"checked": 0, "sold": 0, "detail": [], "no_price": 0}

    rt = get_realtime_prices(positions["ts_code"].tolist())
    sold, no_price = [], 0
    for _, p in positions.iterrows():
        tc = p["ts_code"]
        price = rt.get(tc)
        if price is None:
            no_price += 1
            continue
        avg_cost = float(p["avg_cost"])
        ret = price / avg_cost - 1
        # 1. 硬止损
        if ret <= STOPLOSS:
            execute_sell(account_id, tc, int(p["qty"]), price, trade_date, reason="STOPLOSS")
            sold.append({"ts_code": tc, "reason": "STOPLOSS",
                         "price": price, "ret": round(ret, 4)})
            continue
        # 2. 跌破 MA5（短线趋势走坏）
        ma5 = get_ma(tc, trade_date, 5)
        if ma5 is not None and price < ma5:
            execute_sell(account_id, tc, int(p["qty"]), price, trade_date, reason="MA5")
            sold.append({"ts_code": tc, "reason": "MA5",
                         "price": price, "ret": round(ret, 4)})

    return {"checked": len(positions), "sold": len(sold),
            "detail": sold, "no_price": no_price}


# -------------------- 权益快照 --------------------
def calc_total_equity(account_id: int, trade_date) -> tuple:
    """(cash, market_value, total_equity)"""
    trade_date = _norm_date(trade_date)
    acc = get_account(account_id)
    cash = float(acc["current_cash"])
    positions = get_positions(account_id)
    mv = 0
    for _, p in positions.iterrows():
        price = get_close_price(p["ts_code"], trade_date)
        if price is None:
            price = float(p["avg_cost"])
        mv += float(p["qty"]) * price
    return cash, mv, cash + mv


def save_equity_snapshot(account_id: int, trade_date) -> float:
    """保存今日权益快照（含日收益率）"""
    trade_date = _norm_date(trade_date)
    cash, mv, total = calc_total_equity(account_id, trade_date)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT total_equity FROM paper_equity_daily WHERE account_id=%s "
            "AND trade_date<%s ORDER BY trade_date DESC LIMIT 1",
            (account_id, trade_date))
        last = cur.fetchone()
        daily_ret = (total / float(last[0]) - 1) if last else 0.0
        cur.execute("UPDATE paper_account SET current_equity=%s WHERE account_id=%s",
                   (total, account_id))
        cur.execute(
            "INSERT INTO paper_equity_daily (account_id, trade_date, cash, market_value, "
            "total_equity, daily_return) VALUES (%s,%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE cash=VALUES(cash), market_value=VALUES(market_value), "
            "total_equity=VALUES(total_equity), daily_return=VALUES(daily_return)",
            (account_id, trade_date, cash, mv, total, daily_ret))
        cur.close()
    return total


# -------------------- 查询/报告 --------------------
def get_trades(account_id: int, limit: int = 50) -> pd.DataFrame:
    """成交流水。JOIN market_stock_basic 带名称，并返回精确到秒的 trade_time。"""
    with get_conn() as conn:
        return pd.read_sql(
            "SELECT t.trade_id, t.trade_date, t.trade_time, t.side, "
            "t.ts_code, b.name, t.qty, t.price, t.amount, t.commission, t.reason "
            "FROM paper_trade t "
            "LEFT JOIN market_stock_basic b ON b.ts_code = t.ts_code "
            "WHERE t.account_id=%s "
            "ORDER BY t.trade_time DESC, t.trade_id DESC LIMIT %s",
            conn, params=(account_id, limit))


def get_equity_curve(account_id: int) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql(
            "SELECT trade_date, cash, market_value, total_equity, daily_return "
            "FROM paper_equity_daily WHERE account_id=%s ORDER BY trade_date",
            conn, params=(account_id,))


def daily_report(account_id: int, trade_date=None) -> str:
    """生成单日复盘报告"""
    if trade_date is None:
        trade_date = date.today()
    trade_date = _norm_date(trade_date)
    acc = get_account(account_id)
    if acc is None:
        return f"账户 {account_id} 不存在"

    lines = []
    sep = "=" * 60
    lines.append(sep)
    lines.append(f"  账户 #{account_id}  {acc['account_name']}  策略: {acc['strategy_name']}")
    lines.append(f"  截面日: {trade_date}")
    lines.append(sep)

    cash, mv, total = calc_total_equity(account_id, trade_date)
    init = float(acc["initial_capital"])
    total_ret = (total - init) / init
    lines.append(f"\n  初始资金:    {init:>12,.2f}")
    lines.append(f"  当前现金:    {cash:>12,.2f}")
    lines.append(f"  持仓市值:    {mv:>12,.2f}")
    lines.append(f"  总权益:      {total:>12,.2f}")
    lines.append(f"  累计收益率:  {total_ret*100:>+12.2f}%")

    # 持仓
    positions = get_positions(account_id)
    if not positions.empty:
        lines.append(f"\n  持仓 ({len(positions)} 只):")
        rows = []
        for _, p in positions.iterrows():
            price = get_close_price(p["ts_code"], trade_date) or float(p["avg_cost"])
            ret = price / float(p["avg_cost"]) - 1
            rows.append({
                "代码": p["ts_code"],
                "数量": int(p["qty"]),
                "成本": round(float(p["avg_cost"]), 3),
                "现价": round(price, 3),
                "收益": f"{ret*100:+.2f}%",
                "市值": round(float(p["qty"]) * price, 2),
                "开仓日": p["open_date"],
            })
        df = pd.DataFrame(rows)
        lines.append(df.to_string(index=False))
    else:
        lines.append("\n  当前无持仓")

    # 今日成交
    with get_conn() as conn:
        today_trades = pd.read_sql(
            "SELECT side, ts_code, qty, price, amount, commission, reason "
            "FROM paper_trade WHERE account_id=%s AND trade_date=%s "
            "ORDER BY trade_id",
            conn, params=(account_id, trade_date))
    if not today_trades.empty:
        lines.append(f"\n  今日成交 ({len(today_trades)} 笔):")
        lines.append(today_trades.to_string(index=False))

    lines.append("\n" + sep)
    return "\n".join(lines)
