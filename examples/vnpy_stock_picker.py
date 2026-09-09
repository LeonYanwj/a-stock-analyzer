"""只读的 vn.py Alpha101 选股演示。

从项目根目录运行：

    python -m examples.vnpy_stock_picker --strategy medium_term --limit 10

默认读取项目配置中的 MySQL 行情库。没有 MySQL 时，可以传入一个或多个 CSV：

    python -m examples.vnpy_stock_picker --csv data/600000.csv data/000001.csv

CSV 至少需要 ``ts_code,trade_date,open,high,low,close``，成交量列可使用
``volume`` 或 ``vol``，成交额列可使用 ``amount`` 或 ``turnover``。该程序只
计算和打印候选，不创建交易计划、不下单、不写数据库。
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from astock.vnpy_runtime.signals import VnpyAlphaSignalError, generate_alpha101_signals


def _parse_as_of(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value).replace(hour=0, minute=0, second=0, microsecond=0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--as-of 必须是 YYYY-MM-DD 或 ISO 日期时间") from exc


def _read_rows(rows: Iterable[dict[str, Any]], as_of: datetime | None) -> list[dict[str, Any]]:
    """统一外部文件字段，并丢弃决策日之后的记录。"""
    output = []
    for raw in rows:
        row = {str(k).strip().lower(): v for k, v in dict(raw).items()}
        code = row.get("ts_code") or row.get("symbol") or row.get("code")
        trade_date = row.get("trade_date") or row.get("datetime") or row.get("date")
        if not code or not trade_date:
            continue
        try:
            dt = datetime.fromisoformat(str(trade_date).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            try:
                dt = datetime.strptime(str(trade_date), "%Y%m%d")
            except ValueError:
                continue
        if as_of and dt >= as_of:
            continue
        output.append({
            "ts_code": str(code).strip(),
            "trade_date": dt,
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": row.get("close"),
            "volume": row.get("volume", row.get("vol")),
            "amount": row.get("amount", row.get("turnover")),
            "asset_type": str(row.get("asset_type", "stock")).lower(),
        })
    return output


def _load_csv(paths: list[str], as_of: datetime | None) -> list[dict[str, Any]]:
    rows = []
    for name in paths:
        with open(name, "r", encoding="utf-8-sig", newline="") as handle:
            rows.extend(csv.DictReader(handle))
    return _read_rows(rows, as_of)


def _load_sqlite(path: str, table: str, as_of: datetime | None) -> list[dict[str, Any]]:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
        raise ValueError("--table 只能包含字母、数字和下划线")
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(f"SELECT * FROM {table}").fetchall()
        return _read_rows((dict(row) for row in rows), as_of)
    finally:
        connection.close()


def _mysql_candidates(strategy: str, asset: str, as_of: datetime, limit: int) -> list[dict[str, Any]]:
    from astock.trade_run.repository import MySqlTradeRunRepository
    from astock.trade_run.signal_providers import VnpyAlphaSignalProvider

    repo = MySqlTradeRunRepository.from_config()
    run = {"strategy_code": strategy, "stock_scope": "full", "quick_limit": 100}
    try:
        rows = VnpyAlphaSignalProvider(repo).candidates(run, as_of, {asset} if asset != "all" else {"stock", "etf"})
        return rows[:limit]
    finally:
        repo.close_current_thread()


def _file_candidates(rows: list[dict[str, Any]], strategy: str, asset: str, limit: int) -> list[dict[str, Any]]:
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    if asset == "all":
        for kind in ("stock", "etf"):
            group = [row for row in rows if row.get("asset_type", "stock") == kind]
            if group:
                groups.append((kind, group))
    else:
        groups.append((asset, [row for row in rows if row.get("asset_type", "stock") == asset]))

    output = []
    for kind, group in groups:
        signals = generate_alpha101_signals(group, strategy)
        for item in signals[:limit]:
            symbol, exchange = str(item["vt_symbol"]).rsplit(".", 1)
            ts_code = f"{symbol}.{'SH' if exchange == 'SSE' else 'SZ' if exchange == 'SZSE' else exchange}"
            output.append({
                "ts_code": ts_code,
                "asset_type": kind,
                "reference_price": item["close"],
                "score": item["signal"],
                "factor_count": item["factor_count"],
                "as_of": item["as_of"],
            })
    return sorted(output, key=lambda item: item["score"], reverse=True)[:limit]


def _print_candidates(rows: list[dict[str, Any]], strategy: str, source: str, limit: int) -> None:
    print(f"vn.py Alpha101 只读选股 Demo | strategy={strategy} | source={source}")
    if not rows:
        print("没有生成候选。请确认至少有 3 只标的，并为每只标的准备足够历史日线。")
        return
    print("排名\t代码\t资产\t参考价\t信号分数\t有效因子\t截面日期")
    for index, row in enumerate(rows[:limit], 1):
        as_of = row.get("as_of")
        as_of_text = as_of.strftime("%Y-%m-%d") if hasattr(as_of, "strftime") else str(as_of)[:10]
        print(f"{index}\t{row['ts_code']}\t{row.get('asset_type', '')}\t"
              f"{float(row.get('reference_price', row.get('close', 0))):.4f}\t"
              f"{float(row.get('score', row.get('signal', 0))):.6f}\t"
              f"{row.get('factor_count', '')}\t{as_of_text}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="使用项目内 vn.py Alpha101 做只读横截面选股")
    parser.add_argument("--strategy", choices=("short_term", "medium_term", "long_term"), default="medium_term")
    parser.add_argument("--asset", choices=("stock", "etf", "all"), default="stock")
    parser.add_argument("--as-of", type=_parse_as_of, help="决策日期；只使用此前的日线，默认使用当前日期")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--csv", nargs="+", metavar="FILE", help="一个或多个历史日线 CSV；与 --sqlite 二选一")
    parser.add_argument("--sqlite", metavar="FILE", help="SQLite 文件，默认读取 market_daily 表")
    parser.add_argument("--table", default="market_daily", help="--sqlite 使用的行情表")
    args = parser.parse_args(argv)
    if args.limit <= 0:
        parser.error("--limit 必须大于 0")
    if args.csv and args.sqlite:
        parser.error("--csv 与 --sqlite 不能同时使用")
    as_of = args.as_of or datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        if args.csv:
            rows = _load_csv(args.csv, as_of)
            candidates = _file_candidates(rows, args.strategy, args.asset, args.limit)
            _print_candidates(candidates, args.strategy, "CSV", args.limit)
        elif args.sqlite:
            rows = _load_sqlite(args.sqlite, args.table, as_of)
            candidates = _file_candidates(rows, args.strategy, args.asset, args.limit)
            _print_candidates(candidates, args.strategy, f"SQLite:{args.sqlite}", args.limit)
        else:
            candidates = _mysql_candidates(args.strategy, args.asset, as_of, args.limit)
            _print_candidates(candidates, args.strategy, "MySQL", args.limit)
        return 0
    except (VnpyAlphaSignalError, FileNotFoundError, OSError, RuntimeError) as exc:
        print(f"选股失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
