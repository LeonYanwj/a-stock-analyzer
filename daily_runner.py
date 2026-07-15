"""每日运行器：遍历所有活跃模拟账户，依次执行

每天收盘后定时跑此脚本（cron / 任务计划程序）：
  1. 检查止损（任何一只持仓跌破 -8% 平仓）
  2. 按每日全市场评级复查持仓，连续转弱退出并择优替换（允许空仓）
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


def run_one_account(account: dict, trade_date: date, limit: int = 0,
                    dry_run: bool = False, ratings=None):
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
            price = eng.get_exact_close_price(p["ts_code"], trade_date)
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

    # 2. 每日评级复查：硬风险立即退出，普通转弱连续两天才退出。
    print("\n[2] 每日评级复查...")
    if dry_run:
        print(f"  [DRY] 会按 {strategy} 全市场评级复查持仓并择优替换，允许空仓")
    else:
        if ratings is None:
            from screen import screen_market
            ratings = screen_market(
                strategy=strategy, limit=limit, verbose=True,
                return_all=True, persist_ratings=True)
        r = eng.rebalance_by_rating(aid, trade_date, ratings)
        if r.get("error"):
            print(f"  [warn] 评级复查跳过: {r['error']}")
        else:
            print(f"  保留 {r['kept']} 只 / 卖出 {r['sold']} 只 / 买入 {r['bought']} 只"
                  f"  (目标 {r['top_n']} 只，允许现金仓位)")
            for item in r["sell_detail"]:
                print(f"    卖出检查: {item['ts_code']} {item['action']} {item['reason']}")
            for item in r["buy_detail"]:
                print(f"    买入检查: {item['ts_code']} {item['action']}")

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


def run_all(trade_date=None, account_id: int = None, limit: int = 0,
            dry_run: bool = False):
    """可编程入口：遍历活跃账户跑完整日流程（供调度器 / API / 命令行复用）

    Args:
        trade_date: date 对象、'YYYYMMDD'/'YYYY-MM-DD' 字符串，或 None=今天
        account_id: 只跑某个账户；None 则遍历所有活跃账户
    """
    if trade_date is None:
        trade_date = date.today()
    elif isinstance(trade_date, str):
        s = trade_date.replace("-", "")
        trade_date = date(int(s[:4]), int(s[4:6]), int(s[6:8]))

    if account_id:
        acc = eng.get_account(account_id)
        if acc is None:
            print(f"账户 {account_id} 不存在")
            return
        accounts = [acc]
    else:
        df = eng.list_accounts()
        df = df[df["is_active"] == 1]
        accounts = df.to_dict("records")

    if not accounts:
        print("没有活跃账户")
        return
    print(f"\n共 {len(accounts)} 个活跃账户")

    ratings_by_strategy = {}
    if not dry_run:
        from screen import screen_market
        for strategy in sorted({a["strategy_name"] for a in accounts}):
            print(f"\n生成 {strategy} 全市场每日评级...")
            ratings_by_strategy[strategy] = screen_market(
                strategy=strategy, limit=limit, verbose=True,
                return_all=True, persist_ratings=True)

    for acc in accounts:
        # 补全 account 完整字段（get_account 含 rebal_weeks）
        full = eng.get_account(acc["account_id"]) if "rebal_weeks" not in acc else acc
        run_one_account(
            full, trade_date, limit=limit, dry_run=dry_run,
            ratings=ratings_by_strategy.get(full["strategy_name"]))

    print("\n" + "=" * 60)
    print("  Daily Runner 完成")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="每日运行器")
    parser.add_argument("--date", default=None,
                        help="日期 YYYYMMDD，默认今天")
    parser.add_argument("--account", type=int, default=None,
                        help="只跑某个账户；不传则遍历所有活跃账户")
    parser.add_argument("--limit", type=int, default=0,
                        help="选股阶段股票池规模（0=全部沪深主板）")
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

    # 选账户 + 遍历（复用 run_all）
    run_all(trade_date=trade_date, account_id=args.account,
            limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
