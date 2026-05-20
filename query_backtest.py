"""历史回测查询工具

用法:
    python query_backtest.py                       # 列出最近 20 次回测
    python query_backtest.py --strategy swing      # 只列 swing 策略的
    python query_backtest.py --run 5               # 查看第 5 次回测详情
    python query_backtest.py --run 5 --picks       # 查看第 5 次的持仓明细
    python query_backtest.py --compare 3 5 7       # 对比多次回测
"""
import sys
import io
import argparse

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import pandas as pd

from data.db import get_conn, list_backtest_runs, get_backtest_detail


def show_runs_list(strategy: str = None, limit: int = 20):
    with get_conn() as conn:
        df = list_backtest_runs(conn, strategy_name=strategy, limit=limit)
    if df.empty:
        print("还没有任何回测记录")
        return
    cols_show = ["run_id", "strategy_name", "start_date", "end_date",
                 "ann_return", "sharpe", "max_drawdown", "win_rate",
                 "n_periods", "created_at"]
    cols_show = [c for c in cols_show if c in df.columns]
    df2 = df[cols_show].copy()
    # 友好格式化
    for c in ["ann_return", "max_drawdown", "win_rate"]:
        if c in df2.columns:
            df2[c] = df2[c].apply(lambda x: f"{x*100:+.2f}%" if pd.notna(x) else "—")
    if "sharpe" in df2.columns:
        df2["sharpe"] = df2["sharpe"].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "—")
    pd.set_option("display.width", 200)
    pd.set_option("display.unicode.east_asian_width", True)
    print(df2.to_string(index=False))


def show_run_detail(run_id: int, show_picks: bool = False):
    with get_conn() as conn:
        detail = get_backtest_detail(conn, run_id)
    if detail is None:
        print(f"找不到 run_id={run_id}")
        return

    r = detail["run"]
    print("=" * 60)
    print(f"回测 #{run_id}")
    print("=" * 60)
    print(f"策略 ID:    {r['strategy_id']}")
    print(f"区间:       {r['start_date']} ~ {r['end_date']}")
    print(f"初始净值:   {r['initial_capital']}")
    print(f"最终净值:   {r['final_value']}")
    print(f"年化收益:   {(r['ann_return'] or 0)*100:+.2f}%")
    print(f"夏普:       {r['sharpe'] or 0:.2f}")
    print(f"最大回撤:   {(r['max_drawdown'] or 0)*100:+.2f}%")
    print(f"胜率:       {(r['win_rate'] or 0)*100:.1f}%")
    print(f"调仓次数:   {r['n_periods']}")
    print(f"备注:       {r['note']}")
    print(f"创建时间:   {r['created_at']}")

    if not detail["equity"].empty:
        print(f"\n净值序列: {len(detail['equity'])} 期")
        print(detail["equity"].head(3).to_string(index=False))
        print(f"  ...")
        print(detail["equity"].tail(3).to_string(index=False))

    if not detail["ic"].empty:
        print("\n因子 IC 均值:")
        ic_summary = detail["ic"].groupby("factor_name")["ic"].agg(["mean", "std", "count"])
        ic_summary["IR"] = ic_summary["mean"] / ic_summary["std"]
        print(ic_summary.sort_values("mean", ascending=False).to_string())

    if show_picks and not detail["positions"].empty:
        print(f"\n持仓明细: 共 {len(detail['positions'])} 条")
        # 按调仓日聚合统计
        per_date = detail["positions"].groupby("rebal_date").agg(
            n_picks=("ts_code", "count"),
            avg_return=("period_return", "mean"),
            stoploss=("stoploss_hit", "sum"),
        )
        per_date["avg_return"] = per_date["avg_return"].apply(lambda x: f"{x*100:+.2f}%")
        print(per_date.head(10).to_string())


def compare_runs(run_ids: list):
    with get_conn() as conn:
        rows = []
        for rid in run_ids:
            d = get_backtest_detail(conn, rid)
            if d is None:
                continue
            r = d["run"]
            rows.append({
                "run_id": rid,
                "strategy_id": r["strategy_id"],
                "区间": f"{r['start_date']} ~ {r['end_date']}",
                "年化": f"{(r['ann_return'] or 0)*100:+.2f}%",
                "夏普": f"{r['sharpe'] or 0:.2f}",
                "回撤": f"{(r['max_drawdown'] or 0)*100:+.2f}%",
                "胜率": f"{(r['win_rate'] or 0)*100:.1f}%",
                "持仓数": r["n_periods"],
                "备注": (r["note"] or "")[:40],
            })
    if not rows:
        print("找不到任何记录")
        return
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    pd.set_option("display.unicode.east_asian_width", True)
    print(df.to_string(index=False))


def main():
    parser = argparse.ArgumentParser(description="历史回测查询")
    parser.add_argument("--strategy", help="过滤特定策略名")
    parser.add_argument("--limit", type=int, default=20, help="列出最近 N 次")
    parser.add_argument("--run", type=int, help="查看特定 run_id 详情")
    parser.add_argument("--picks", action="store_true", help="显示持仓明细")
    parser.add_argument("--compare", type=int, nargs="+", help="对比多个 run_id")
    args = parser.parse_args()

    if args.compare:
        compare_runs(args.compare)
    elif args.run is not None:
        show_run_detail(args.run, show_picks=args.picks)
    else:
        show_runs_list(strategy=args.strategy, limit=args.limit)


if __name__ == "__main__":
    main()
