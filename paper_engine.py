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


def list_accounts() -> pd.DataFrame:
    with get_conn() as conn:
        df = pd.read_sql(
            "SELECT a.account_id, a.account_name, s.strategy_name, "
            "a.initial_capital, a.current_cash, a.current_equity, "
            "ROUND((a.current_equity - a.initial_capital) / a.initial_capital * 100, 2) AS return_pct, "
            "a.started_at, a.is_active "
            "FROM paper_account a JOIN strategy_config s USING(strategy_id) "
            "ORDER BY a.started_at DESC", conn)
        return df


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


# -------------------- 持仓 --------------------
def get_positions(account_id: int) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql(
            "SELECT * FROM paper_position WHERE account_id=%s ORDER BY ts_code",
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


def sell_all(account_id: int, trade_date, reason: str = "REBALANCE") -> dict:
    """清仓"""
    positions = get_positions(account_id)
    if positions.empty:
        return {"n_sold": 0, "total_revenue": 0}
    trade_date = _norm_date(trade_date)
    total_rev = 0
    n = 0
    for _, p in positions.iterrows():
        price = get_close_price(p["ts_code"], trade_date)
        if price is None:
            print(f"  [skip] {p['ts_code']} 当日无价")
            continue
        r = execute_sell(account_id, p["ts_code"], int(p["qty"]), price, trade_date, reason)
        total_rev += r["revenue"]
        n += 1
    return {"n_sold": n, "total_revenue": total_rev}


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
    with get_conn() as conn:
        return pd.read_sql(
            "SELECT trade_id, trade_date, side, ts_code, qty, price, amount, "
            "commission, reason FROM paper_trade WHERE account_id=%s "
            "ORDER BY trade_date DESC, trade_id DESC LIMIT %s",
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
