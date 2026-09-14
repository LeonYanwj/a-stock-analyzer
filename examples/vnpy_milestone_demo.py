"""第一里程碑 Demo：使用真实 MySQL 行情验证 vn.py Alpha101 选股。

从项目根目录、远程服务器的 vn.py 虚拟环境运行：

    python -m examples.vnpy_milestone_demo --strategy medium_term

脚本只读访问 ``config.py`` 配置的 MySQL，读取合格 A 股主板历史日线，并通过项目
生产信号提供器真实调用 ``vnpy.alpha`` 的 Alpha101 表达式生成候选排名。它不连接
券商、不读取迅投 Token、不写数据库，也不会创建交易计划或下单。
"""
from __future__ import annotations

import argparse
import importlib.metadata
import sys
from datetime import date, datetime, timedelta
from typing import Any, Iterable


MAIN_BOARD_FILTER = (
    "b.is_active=1 AND b.is_st=0 AND "
    "(b.symbol LIKE ? OR b.symbol LIKE ? OR b.symbol LIKE ? OR b.symbol LIKE ? "
    "OR b.symbol LIKE ? OR b.symbol LIKE ? OR b.symbol LIKE ?)"
)
MAIN_BOARD_PREFIXES = ("600%", "601%", "603%", "605%", "000%", "001%", "002%")
MIN_HISTORY_ROWS = 260


def _package_version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _check_runtime() -> None:
    """确认选股所需的 vn.py 核心和 Alpha 模块可以真实导入。"""
    try:
        import polars  # noqa: F401
        import vnpy  # noqa: F401
        from vnpy.alpha.dataset.datasets.alpha_101 import Alpha101  # noqa: F401
        from vnpy.alpha.dataset.utility import calculate_by_expression  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "vn.py Alpha 运行依赖不完整；请在远程服务器的 vn.py 虚拟环境中"
            "安装 requirements.txt"
        ) from exc

    print(
        "环境检查通过："
        f"Python {sys.version.split()[0]}, "
        f"vnpy {_package_version('vnpy')}, "
        f"polars {_package_version('polars')}"
    )


def _parse_as_of(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--as-of 必须是 YYYY-MM-DD 或 ISO 日期时间") from exc


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _load_coverage(repository: Any, cutoff: date) -> dict[str, Any]:
    """只读核验生产选股所使用的股票池和日线覆盖。"""
    coverage_sql = (
        "SELECT COUNT(*) AS bar_count, COUNT(DISTINCT d.ts_code) AS symbol_count, "
        "MIN(d.trade_date) AS first_trade_date, MAX(d.trade_date) AS latest_trade_date "
        "FROM market_daily d JOIN market_stock_basic b ON b.ts_code=d.ts_code "
        f"WHERE d.trade_date<=? AND d.adjust=? AND {MAIN_BOARD_FILTER}"
    )
    coverage_params = (cutoff.isoformat(), "qfq", *MAIN_BOARD_PREFIXES)
    coverage = repository.conn.execute(coverage_sql, coverage_params).fetchone()
    result = dict(coverage or {})

    ready_sql = (
        "SELECT COUNT(*) AS ready_symbol_count FROM ("
        "SELECT d.ts_code FROM market_daily d "
        "JOIN market_stock_basic b ON b.ts_code=d.ts_code "
        f"WHERE d.trade_date<=? AND d.adjust=? AND {MAIN_BOARD_FILTER} "
        "GROUP BY d.ts_code HAVING COUNT(*)>=?"
        ") ready"
    )
    ready_params = (cutoff.isoformat(), "qfq", *MAIN_BOARD_PREFIXES, MIN_HISTORY_ROWS)
    ready = repository.conn.execute(ready_sql, ready_params).fetchone()
    result["ready_symbol_count"] = int((ready or {}).get("ready_symbol_count") or 0)
    return result


def _validate_coverage(
    coverage: dict[str, Any],
    cutoff: date,
    max_data_lag_days: int,
    allow_stale: bool,
) -> int:
    latest_value = coverage.get("latest_trade_date")
    if not latest_value or int(coverage.get("symbol_count") or 0) < 3:
        raise RuntimeError("MySQL 没有至少 3 只合格主板股票的真实复权日线")
    if int(coverage.get("ready_symbol_count") or 0) < 3:
        raise RuntimeError(f"不足 3 只股票具备至少 {MIN_HISTORY_ROWS} 条历史日线")
    latest = _as_date(latest_value)
    lag_days = (cutoff - latest).days
    if lag_days < 0:
        raise RuntimeError("行情数据日期晚于选股截面，数据截面校验失败")
    if lag_days > max_data_lag_days and not allow_stale:
        raise RuntimeError(
            f"行情数据距离选股截面已滞后 {lag_days} 天；请先更新 market_daily，"
            "或仅在明确进行历史演示时传 --allow-stale"
        )
    return lag_days


def _validate_candidates(candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = list(candidates)
    if not rows:
        raise RuntimeError("vn.py Alpha101 没有从真实行情生成候选")
    scores = [float(row["score"]) for row in rows]
    if scores != sorted(scores, reverse=True):
        raise RuntimeError("vn.py Alpha101 候选没有按信号分数降序排列")
    invalid_sources = {str(row.get("data_source")) for row in rows} - {"vnpy_alpha101"}
    if invalid_sources:
        raise RuntimeError(f"候选并非全部来自 vn.py Alpha101：{sorted(invalid_sources)}")
    return rows


def _run_real_selection(
    strategy: str,
    as_of: datetime,
    stock_scope: str,
    quick_limit: int,
    limit: int,
    max_data_lag_days: int,
    allow_stale: bool,
) -> None:
    from astock.trade_run.repository import MySqlTradeRunRepository
    from astock.trade_run.signal_providers import VnpyAlphaSignalProvider

    repository = MySqlTradeRunRepository.from_config()
    cutoff = as_of.date() - timedelta(days=1)
    try:
        print("[2/3] 只读核验 MySQL 真实行情覆盖", flush=True)
        coverage = _load_coverage(repository, cutoff)
        lag_days = _validate_coverage(
            coverage, cutoff, max_data_lag_days=max_data_lag_days, allow_stale=allow_stale
        )
        print(
            "数据截面："
            f"decision_as_of={as_of.isoformat()}, cutoff={cutoff.isoformat()}, "
            f"latest_trade_date={coverage['latest_trade_date']}, lag_days={lag_days}"
        )
        print(
            "股票池覆盖："
            f"eligible_symbols={int(coverage.get('symbol_count') or 0)}, "
            f"ready_symbols={coverage['ready_symbol_count']}, "
            f"bars={int(coverage.get('bar_count') or 0)}, "
            f"scope={stock_scope}, quick_limit={quick_limit if stock_scope == 'quick' else '-'}"
        )

        print("[3/3] 使用生产信号链真实执行 vn.py Alpha101", flush=True)
        provider = VnpyAlphaSignalProvider(repository)
        provider.candidate_limit = limit
        run = {
            "strategy_code": strategy,
            "stock_scope": stock_scope,
            "quick_limit": quick_limit,
        }
        candidates = _validate_candidates(provider.candidates(run, as_of, {"stock"}))
    finally:
        repository.close_current_thread()

    print(
        f"真实选股完成：strategy={strategy}, candidates={len(candidates)}, "
        "data_source=vnpy_alpha101"
    )
    print("  排名  股票代码       信号分数  参考价     入选依据")
    for rank, row in enumerate(candidates[:limit], 1):
        print(
            f"  {rank:>2}    {row['ts_code']:<12} "
            f"{float(row['score']):.6f}  {float(row['reference_price']):>8.3f}  "
            f"{row.get('reason', '')}"
        )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="使用 MySQL 真实 A 股日线验证 vn.py Alpha101 选股（全程只读）"
    )
    parser.add_argument(
        "--strategy",
        choices=("short_term", "medium_term", "long_term"),
        default="medium_term",
        help="vn.py Alpha101 内置组合，默认 medium_term",
    )
    parser.add_argument(
        "--as-of",
        type=_parse_as_of,
        help="决策截面；默认当前时间，选股只使用该日期之前的日线",
    )
    parser.add_argument(
        "--stock-scope", choices=("quick", "full"), default="quick",
        help="quick 按最新成交额扫描前 N 只，full 扫描全部合格主板",
    )
    parser.add_argument("--quick-limit", type=int, default=100, help="快速扫描股票数，50–500")
    parser.add_argument("--limit", type=int, default=10, help="输出候选数量，1–50")
    parser.add_argument(
        "--max-data-lag-days", type=int, default=10,
        help="行情相对决策截面允许的最大自然日滞后，默认 10",
    )
    parser.add_argument(
        "--allow-stale", action="store_true",
        help="允许使用超过最大滞后天数的数据，仅用于明确的历史演示",
    )
    args = parser.parse_args(argv)
    if not 50 <= args.quick_limit <= 500:
        parser.error("--quick-limit 必须在 50–500 之间")
    if not 1 <= args.limit <= 50:
        parser.error("--limit 必须在 1–50 之间")
    if args.max_data_lag_days < 0:
        parser.error("--max-data-lag-days 不能小于 0")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    as_of = args.as_of or datetime.now().replace(microsecond=0)
    try:
        print("[1/3] 检查远程 vn.py Alpha 环境", flush=True)
        _check_runtime()
        _run_real_selection(
            strategy=args.strategy,
            as_of=as_of,
            stock_scope=args.stock_scope,
            quick_limit=args.quick_limit,
            limit=args.limit,
            max_data_lag_days=args.max_data_lag_days,
            allow_stale=args.allow_stale,
        )
    except Exception as exc:
        print(f"DEMO FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("MILESTONE DEMO OK: 已使用 MySQL 真实行情完成 vn.py Alpha101 选股")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
