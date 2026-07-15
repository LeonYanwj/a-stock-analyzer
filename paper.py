"""模拟盘 CLI

用法:
    python paper.py create --name "swing-A" --capital 100000 --strategy swing
    python paper.py list                                              # 列出所有账户
    python paper.py positions --account 1                             # 看持仓
    python paper.py trades --account 1 [--limit 20]                   # 看成交
    python paper.py buy  --account 1 --code 600487 --qty 200 --price 18.5 [--date 20260518]
    python paper.py sell --account 1 --code 600487 --qty 200 --price 19.2
    python paper.py rebalance --account 1 --picks 600487 002028 000001 --date 20260518
    python paper.py stoploss --account 1 [--date 20260518]            # 触发止损检查
    python paper.py snapshot --account 1 [--date 20260518]            # 存今日权益快照
    python paper.py report --account 1 [--date 20260518]              # 生成复盘报告
"""
import sys
import io
import argparse

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import pandas as pd

import paper_engine as eng


def cmd_create(args):
    aid = eng.create_account(args.name, args.capital, args.strategy)
    print(f"[OK] 账户已创建: account_id={aid}, 资金 {args.capital:,.0f}, 策略 {args.strategy}")


def cmd_list(args):
    df = eng.list_accounts()
    if df.empty:
        print("还没有任何账户")
        return
    pd.set_option("display.width", 200)
    pd.set_option("display.unicode.east_asian_width", True)
    print(df.to_string(index=False))


def cmd_positions(args):
    df = eng.get_positions(args.account)
    if df.empty:
        print("当前无持仓")
        return
    # 加现价 + 收益
    today = pd.Timestamp.now().date()
    rows = []
    for _, p in df.iterrows():
        price = eng.get_close_price(p["ts_code"], today) or float(p["avg_cost"])
        ret = price / float(p["avg_cost"]) - 1
        rows.append({
            "代码": p["ts_code"],
            "数量": int(p["qty"]),
            "成本": round(float(p["avg_cost"]), 3),
            "现价": round(price, 3),
            "收益率": f"{ret*100:+.2f}%",
            "市值": round(float(p["qty"]) * price, 2),
            "开仓日": p["open_date"],
        })
    out = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    pd.set_option("display.unicode.east_asian_width", True)
    print(out.to_string(index=False))


def cmd_trades(args):
    df = eng.get_trades(args.account, limit=args.limit)
    if df.empty:
        print("还没有任何成交")
        return
    pd.set_option("display.width", 200)
    print(df.to_string(index=False))


def cmd_buy(args):
    r = eng.execute_buy(args.account, args.code, args.qty, args.price,
                       trade_date=args.date, reason=args.reason)
    print(f"[OK] 买入 {args.code} × {args.qty}, 实际价 {r['price']:.3f}, "
          f"总成本 {r['cost']:,.2f}, 佣金 {r['commission']:.2f}")


def cmd_sell(args):
    r = eng.execute_sell(args.account, args.code, args.qty, args.price,
                        trade_date=args.date, reason=args.reason)
    print(f"[OK] 卖出 {args.code} × {args.qty}, 实际价 {r['price']:.3f}, "
          f"净收入 {r['revenue']:,.2f}, 含费 {r['fees']:.2f}")


def cmd_rebalance(args):
    """调仓：清仓 + 等权买入新的 picks"""
    print(f"[调仓] 账户 {args.account}, 截面日 {args.date or '今天'}")
    sold = eng.sell_all(args.account, args.date, reason="REBALANCE")
    print(f"  卖出 {sold['n_sold']} 只, 总收入 {sold['total_revenue']:,.2f}")
    bought = eng.buy_equal_weight(args.account, args.picks, args.date, reason="REBALANCE")
    print(f"  买入 {bought['n_bought']} 只, 总支出 {bought['total_spent']:,.2f}")
    if bought["skipped"]:
        print("  跳过的:")
        for tc, why in bought["skipped"]:
            print(f"    {tc}: {why}")
    # 保存快照
    total = eng.save_equity_snapshot(args.account, args.date)
    print(f"  当前总权益: {total:,.2f}")


def cmd_auto_rebalance(args):
    """自动调仓：全市场评级 + 持仓日评 + 择优替换。"""
    account = eng.get_account(args.account)
    if account is None:
        print(f"账户 {args.account} 不存在")
        return

    print(f"[1/3] 跑 {account['strategy_name']} 全市场评级...")
    from screen import screen_market
    ratings = screen_market(
        strategy=account["strategy_name"],
        capital=float(account["current_equity"]),
        limit=args.limit,
        verbose=True,
        return_all=True,
        persist_ratings=True,
    )
    if ratings.empty:
        print("  评级结果为空，跳过调仓")
        return
    print(f"  完成 {len(ratings)} 只股票评级")

    print(f"\n[2/3] 复查持仓并择优替换...")
    result = eng.rebalance_by_rating(args.account, args.date, ratings)
    if result.get("error"):
        print(f"  跳过: {result['error']}")
    else:
        print(f"  保留 {result['kept']} / 卖出 {result['sold']} / 买入 {result['bought']}"
              f"（目标 {result['top_n']}，允许空仓）")

    print(f"\n[3/3] 保存权益快照...")
    total = eng.save_equity_snapshot(args.account, args.date)
    print(f"  当前总权益: {total:,.2f}")


def cmd_stoploss(args):
    r = eng.check_stoploss(args.account, args.date)
    if r["triggered"] == 0:
        print("[OK] 无持仓触发止损")
    else:
        print(f"[!] {r['triggered']} 只触发止损:")
        for d in r["details"]:
            print(f"  {d['ts_code']}: 收益 {d['ret']*100:+.2f}%, 平仓价 {d['price']:.3f}")


def cmd_snapshot(args):
    total = eng.save_equity_snapshot(args.account, args.date)
    cash, mv, _ = eng.calc_total_equity(args.account, args.date)
    print(f"[OK] 快照已保存：现金 {cash:,.2f}, 持仓市值 {mv:,.2f}, 总权益 {total:,.2f}")


def cmd_report(args):
    print(eng.daily_report(args.account, args.date))


def main():
    parser = argparse.ArgumentParser(description="模拟盘 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create", help="创建账户")
    p.add_argument("--name", required=True)
    p.add_argument("--capital", type=float, required=True)
    p.add_argument("--strategy", required=True)
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("list", help="列出所有账户")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("positions", help="查看持仓")
    p.add_argument("--account", type=int, required=True)
    p.set_defaults(func=cmd_positions)

    p = sub.add_parser("trades", help="查看成交")
    p.add_argument("--account", type=int, required=True)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_trades)

    p = sub.add_parser("buy", help="手动买入")
    p.add_argument("--account", type=int, required=True)
    p.add_argument("--code", required=True)
    p.add_argument("--qty", type=int, required=True)
    p.add_argument("--price", type=float, required=True)
    p.add_argument("--date", default=None)
    p.add_argument("--reason", default="MANUAL")
    p.set_defaults(func=cmd_buy)

    p = sub.add_parser("sell", help="手动卖出")
    p.add_argument("--account", type=int, required=True)
    p.add_argument("--code", required=True)
    p.add_argument("--qty", type=int, required=True)
    p.add_argument("--price", type=float, required=True)
    p.add_argument("--date", default=None)
    p.add_argument("--reason", default="MANUAL")
    p.set_defaults(func=cmd_sell)

    p = sub.add_parser("rebalance", help="清仓 + 等权买入新选股（手动传入 picks）")
    p.add_argument("--account", type=int, required=True)
    p.add_argument("--picks", nargs="+", required=True, help="股票代码列表（含交易所，如 600487.SH）")
    p.add_argument("--date", default=None)
    p.set_defaults(func=cmd_rebalance)

    p = sub.add_parser("auto-rebalance", help="自动调仓：全市场评级 + 持仓日评 + 择优替换")
    p.add_argument("--account", type=int, required=True)
    p.add_argument("--limit", type=int, default=0, help="股票池规模（0=全部沪深主板）")
    p.add_argument("--date", default=None)
    p.set_defaults(func=cmd_auto_rebalance)

    p = sub.add_parser("stoploss", help="检查止损")
    p.add_argument("--account", type=int, required=True)
    p.add_argument("--date", default=None)
    p.set_defaults(func=cmd_stoploss)

    p = sub.add_parser("snapshot", help="保存今日权益快照")
    p.add_argument("--account", type=int, required=True)
    p.add_argument("--date", default=None)
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("report", help="生成复盘报告")
    p.add_argument("--account", type=int, required=True)
    p.add_argument("--date", default=None)
    p.set_defaults(func=cmd_report)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
