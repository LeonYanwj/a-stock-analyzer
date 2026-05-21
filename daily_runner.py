"""每日运行器：遍历所有活跃模拟账户，依次执行

每天收盘后定时跑此脚本（cron / 任务计划程序）：
  1. 检查止损（任何一只持仓跌破 -8% 平仓）
  2. 是否调仓日 → 自动调仓（跑 screen + 清仓 + 等权买入）
  3. 保存当日权益快照
  4. 输出每个账户的复盘报告

用法:
    python daily_runner.py                          # 用今天日期
    python daily_runner.py --date 20260518          # 历史日期复盘
    python daily_runner.py --account 1              # 只跑某个账户
    python daily_runner.py --dry-run                # 只看会做什么，不写入
"""
import sys
import io
import argparse
from datetime import date, datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import paper_engine as eng


def run_one_account(account: dict, trade_date: date, limit: int = 500,
                    dry_run: bool = False):
    """对一个账户跑完整日流程"""
    aid = account["account_id"]
    name = account["account_name"]
    strategy = account["strategy_name"]

    sep = ">" * 60
    print(f"\n{sep}")
    print(f"  账户 #{aid}  {name}  ({strategy})  日期 {trade_date}")
    print(sep)

    # 1. 止损
    print("\n[1] 检查止损...")
    if dry_run:
        positions = eng.get_positions(aid)
        triggered = 0
        for _, p in positions.iterrows():
            price = eng.get_close_price(p["ts_code"], trade_date)
            if price is None:
                continue
            ret = price / float(p["avg_cost"]) - 1
            if ret <= eng.STOPLOSS:
                triggered += 1
                print(f"  [DRY] 会平仓 {p['ts_code']}（{ret*100:+.2f}%）")
        if triggered == 0:
            print("  无止损触发")
    else:
        r = eng.check_stoploss(aid, trade_date)
        if r["triggered"] == 0:
            print("  无止损触发")
        else:
            print(f"  {r['triggered']} 只触发止损:")
            for d in r["details"]:
                print(f"    {d['ts_code']}  收益 {d['ret']*100:+.2f}%  价 {d['price']:.3f}")

    # 2. 是否调仓日
    is_rebal = eng.is_rebal_day(aid, trade_date)
    print(f"\n[2] 调仓日判断: {'是' if is_rebal else '否（继续持有）'}")
    if is_rebal:
        if dry_run:
            print(f"  [DRY] 会跑 {strategy} 选股 + 清仓 + 等权买入")
        else:
            print(f"  跑 {strategy} 选股...")
            from screen import screen_market
            picks_df = screen_market(
                strategy=strategy,
                capital=float(account["current_equity"]),
                limit=limit,
                verbose=False,
            )
            if picks_df.empty:
                print("  [warn] 选股为空，跳过调仓")
            else:
                picks = picks_df.index.tolist()
                print(f"  选出 {len(picks)} 只")
                sold = eng.sell_all(aid, trade_date, reason="REBALANCE")
                print(f"  卖出 {sold['n_sold']} 只, 收入 {sold['total_revenue']:,.2f}")
                bought = eng.buy_equal_weight(aid, picks, trade_date, reason="REBALANCE")
                print(f"  买入 {bought['n_bought']} 只, 支出 {bought['total_spent']:,.2f}")
                if bought["skipped"]:
                    print(f"  跳过 {len(bought['skipped'])} 只（停牌/资金不够）")

    # 3. 权益快照
    print("\n[3] 权益快照")
    if dry_run:
        cash, mv, total = eng.calc_total_equity(aid, trade_date)
        print(f"  [DRY] 现金 {cash:,.2f}, 持仓市值 {mv:,.2f}, 总权益 {total:,.2f}")
    else:
        total = eng.save_equity_snapshot(aid, trade_date)
        print(f"  总权益 {total:,.2f}")

    # 4. 复盘报告
    print("\n[4] 复盘报告:")
    print(eng.daily_report(aid, trade_date))


def main():
    parser = argparse.ArgumentParser(description="每日运行器")
    parser.add_argument("--date", default=None,
                        help="日期 YYYYMMDD，默认今天")
    parser.add_argument("--account", type=int, default=None,
                        help="只跑某个账户；不传则遍历所有活跃账户")
    parser.add_argument("--limit", type=int, default=500,
                        help="选股阶段股票池规模（默认 500）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只看会做什么，不实际写入")
    args = parser.parse_args()

    # 日期解析
    if args.date:
        s = args.date.replace("-", "")
        trade_date = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    else:
        trade_date = date.today()

    print("=" * 60)
    print(f"  Daily Runner   日期 {trade_date}"
          + ("   [DRY-RUN]" if args.dry_run else ""))
    print("=" * 60)

    # 选账户
    if args.account:
        acc = eng.get_account(args.account)
        if acc is None:
            print(f"账户 {args.account} 不存在")
            return
        accounts = [acc]
    else:
        df = eng.list_accounts()
        df = df[df["is_active"] == 1]
        accounts = df.to_dict("records")
        # 补 strategy_name 等（list_accounts 已返回）

    if not accounts:
        print("没有活跃账户")
        return
    print(f"\n共 {len(accounts)} 个活跃账户")

    # 逐个跑
    for acc in accounts:
        # 补全 account 完整字段（get_account 含 rebal_weeks）
        full = eng.get_account(acc["account_id"]) if "rebal_weeks" not in acc else acc
        run_one_account(full, trade_date, limit=args.limit, dry_run=args.dry_run)

    print("\n" + "=" * 60)
    print("  Daily Runner 完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
