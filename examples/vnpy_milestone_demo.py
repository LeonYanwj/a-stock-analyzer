"""第一里程碑 Demo：使用真实 A 股日线验证 vn.py Alpha101 选股。

从项目根目录、远程服务器的 vn.py 虚拟环境运行：

    python -m examples.vnpy_milestone_demo --strategy medium_term

默认先只读检查 MySQL；行情不足时对当前截面从 AKShare 获取主板股票池和历史日线。
历史 --as-of 不会使用当前股票池回填，以防未来数据泄漏。两条路径都调用
``vnpy.alpha`` 的 Alpha101 表达式。脚本不连接券商、不读取迅投 Token、不写数据库、
不创建交易计划或下单。
"""
from __future__ import annotations

import argparse
import importlib.metadata
import sys
from datetime import date, datetime, timedelta
from math import isfinite
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


def _load_coverage(repository: Any, cutoff: date, fresh_since: date | None = None) -> dict[str, Any]:
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
    if fresh_since is not None:
        fresh_sql = ready_sql.replace("HAVING COUNT(*)>=?", "HAVING COUNT(*)>=? AND MAX(d.trade_date)>=?")
        fresh_params = (*ready_params, fresh_since.isoformat())
        fresh = repository.conn.execute(fresh_sql, fresh_params).fetchone()
        result["fresh_ready_symbol_count"] = int((fresh or {}).get("ready_symbol_count") or 0)
    return result


def _validate_coverage(
    coverage: dict[str, Any],
    cutoff: date,
    max_data_lag_days: int,
    allow_stale: bool,
    required_symbols: int = 3,
) -> int:
    latest_value = coverage.get("latest_trade_date")
    if not latest_value or int(coverage.get("symbol_count") or 0) < 3:
        raise RuntimeError("MySQL 没有至少 3 只合格主板股票的真实复权日线")
    count_key = "ready_symbol_count" if allow_stale else "fresh_ready_symbol_count"
    ready_count = int(coverage.get(count_key, coverage.get("ready_symbol_count")) or 0)
    if ready_count < required_symbols:
        raise RuntimeError(
            f"仅 {ready_count} 只股票具备至少 {MIN_HISTORY_ROWS} 条"
            f"{'新鲜' if not allow_stale else ''}历史日线；"
            f"此扫描范围至少需要 {required_symbols} 只，不能按完整股票池验收"
        )
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


def _snapshot_symbols(snapshot: Iterable[dict[str, Any]], quick_limit: int) -> list[str]:
    """按当前成交额排序主板股票，不以不完整快照冒充全市场。"""
    ranked: dict[str, float] = {}
    for row in snapshot:
        symbol = str(row.get("代码") or "").strip()
        name = str(row.get("名称") or "").upper()
        if not symbol.startswith(tuple(prefix[:3] for prefix in MAIN_BOARD_PREFIXES)):
            continue
        if len(symbol) != 6 or "ST" in name or "退" in name:
            continue
        try:
            amount = float(row.get("成交额") or 0)
        except (TypeError, ValueError):
            continue
        if isfinite(amount) and amount > 0:
            if symbol in ranked:
                raise RuntimeError(f"AKShare 股票快照存在重复代码：{symbol}")
            ranked[symbol] = amount
    if len(ranked) < quick_limit:
        raise RuntimeError(
            f"AKShare 当前主板快照仅有 {len(ranked)} 只有效股票，"
            f"不足指定 quick_limit={quick_limit}；不能按完整股票池验收"
        )
    ordered = sorted(ranked.items(), key=lambda item: (-item[1], item[0]))
    return [symbol for symbol, _ in ordered[:quick_limit]]


def _history_bars(symbol: str, history: Iterable[dict[str, Any]], cutoff: date) -> list[dict[str, Any]]:
    """只接收完整、无重复、截面之前的真实日线；东财成交量单位为手。"""
    bars: dict[date, dict[str, Any]] = {}
    for row in history:
        trade_date = _as_date(row["日期"])
        if trade_date > cutoff:
            continue
        if trade_date in bars:
            raise RuntimeError(f"{symbol} 历史行情出现重复交易日：{trade_date}")
        try:
            values = {key: float(row[column]) for key, column in (
                ("open", "开盘"), ("high", "最高"), ("low", "最低"),
                ("close", "收盘"), ("volume", "成交量"), ("amount", "成交额"),
            )}
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"{symbol} 历史行情字段缺失或非数值") from exc
        if not all(isfinite(value) and value > 0 for value in values.values()):
            raise RuntimeError(f"{symbol} 在 {trade_date} 有无效量价数据")
        values["volume"] *= 100  # stock_zh_a_hist 成交量为手，vn.py 输入为股
        bars[trade_date] = {"ts_code": f"{symbol}.{'SH' if symbol.startswith('6') else 'SZ'}",
                            "trade_date": trade_date, **values}
    return [bars[key] for key in sorted(bars)]


def _load_akshare_bars(cutoff: date, quick_limit: int, max_data_lag_days: int,
                       allow_stale: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("AKShare 未安装；请在运行 Demo 的同一虚拟环境安装 requirements.txt") from exc
    try:
        snapshot = ak.stock_zh_a_spot_em().to_dict("records")
    except Exception as exc:
        raise RuntimeError(f"AKShare 当前 A 股快照获取失败：{type(exc).__name__}: {exc}") from exc
    symbols = _snapshot_symbols(snapshot, quick_limit)
    start_date = (cutoff - timedelta(days=600)).strftime("%Y%m%d")
    end_date = cutoff.strftime("%Y%m%d")
    ready: dict[str, list[dict[str, Any]]] = {}
    failed: list[str] = []
    for index, symbol in enumerate(symbols, 1):
        try:
            history = ak.stock_zh_a_hist(
                symbol=symbol, period="daily", start_date=start_date,
                end_date=end_date, adjust="qfq",
            ).to_dict("records")
            bars = _history_bars(symbol, history, cutoff)
            if len(bars) < MIN_HISTORY_ROWS:
                raise RuntimeError(f"历史日线不足 {MIN_HISTORY_ROWS} 条")
            lag_days = (cutoff - bars[-1]["trade_date"]).days
            if lag_days > max_data_lag_days and not allow_stale:
                raise RuntimeError(f"历史日线滞后 {lag_days} 天")
            ready[symbol] = bars[-320:]
        except Exception as exc:
            failed.append(f"{symbol}({type(exc).__name__}: {exc})")
        if index % 10 == 0 or index == len(symbols):
            print(f"  AKShare 日线进度：{index}/{len(symbols)}，有效 {len(ready)}，失败 {len(failed)}", flush=True)
    if not ready:
        raise RuntimeError("AKShare 没有获得可用于 vn.py Alpha101 的有效历史日线")
    latest = max(bars[-1]["trade_date"] for bars in ready.values())
    aligned = {symbol: bars for symbol, bars in ready.items()
               if bars[-1]["trade_date"] == latest}
    if len(aligned) < 3 or len(aligned) < quick_limit * 0.8:
        sample = "; ".join(failed[:3])
        raise RuntimeError(
            f"AKShare 同一交易日有效股票仅 {len(aligned)}/{quick_limit}，"
            f"不能当作完整选股池；失败示例：{sample or '最新交易日不一致'}"
        )
    bars = [row for symbol in symbols if symbol in aligned for row in aligned[symbol]]
    return bars, {"selected": len(symbols), "ready": len(aligned), "failed": len(failed),
                  "latest_trade_date": latest, "bars": len(bars)}


def _run_akshare_selection(strategy: str, as_of: datetime, stock_scope: str,
                           quick_limit: int, limit: int, max_data_lag_days: int,
                           allow_stale: bool) -> list[dict[str, Any]]:
    if stock_scope != "quick":
        raise RuntimeError("AKShare 按需回退仅支持 quick；全市场需先批量准备行情，不能逐只即时请求")
    from astock.trade_run.signal_providers import VnpyAlphaSignalProvider
    from astock.vnpy_runtime.signals import generate_alpha101_signals

    cutoff = as_of.date() - timedelta(days=1)
    print("[2/3] 从 AKShare 获取当前主板快照和真实历史日线（不写 MySQL）", flush=True)
    bars, coverage = _load_akshare_bars(cutoff, quick_limit, max_data_lag_days, allow_stale)
    print(
        "数据截面："
        f"decision_as_of={as_of.isoformat()}, cutoff={cutoff.isoformat()}, "
        f"latest_trade_date={coverage['latest_trade_date']}, "
        f"ready_symbols={coverage['ready']}/{coverage['selected']}, bars={coverage['bars']}, "
        f"failed_symbols={coverage['failed']}, market_source=AKShare/东方财富"
    )
    print("[3/3] 使用真实 AKShare 日线执行 vn.py Alpha101", flush=True)
    signals = generate_alpha101_signals(bars, strategy)
    provider = VnpyAlphaSignalProvider()
    provider.candidate_limit = limit
    return _validate_candidates(provider._candidate_rows(signals, "stock", as_of))


def _run_real_selection(
    strategy: str,
    as_of: datetime,
    stock_scope: str,
    quick_limit: int,
    limit: int,
    max_data_lag_days: int,
    allow_stale: bool,
    data_source: str = "auto",
    current_snapshot: bool = True,
) -> None:
    candidates = None
    market_source = "MySQL"
    if data_source != "akshare":
        repository = None
        mysql_ready = False
        try:
            from astock.trade_run.repository import MySqlTradeRunRepository
            from astock.trade_run.signal_providers import VnpyAlphaSignalProvider

            repository = MySqlTradeRunRepository.from_config()
            cutoff = as_of.date() - timedelta(days=1)
            print("[2/3] 只读核验 MySQL 真实行情覆盖", flush=True)
            fresh_since = cutoff - timedelta(days=max_data_lag_days)
            coverage = _load_coverage(repository, cutoff, fresh_since=fresh_since)
            required_symbols = max(3, int(quick_limit * 0.8)) if stock_scope == "quick" else 3
            lag_days = _validate_coverage(
                coverage, cutoff, max_data_lag_days=max_data_lag_days,
                allow_stale=allow_stale, required_symbols=required_symbols,
            )
            mysql_ready = True
        except Exception as exc:
            if data_source == "mysql":
                if repository is not None:
                    repository.close_current_thread()
                raise
            print(f"MySQL 行情未就绪：{type(exc).__name__}: {exc}；尝试 AKShare 按需获取", flush=True)
        if mysql_ready:
            try:
                print(
                    "数据截面："
                    f"decision_as_of={as_of.isoformat()}, cutoff={cutoff.isoformat()}, "
                    f"latest_trade_date={coverage['latest_trade_date']}, lag_days={lag_days}"
                )
                print(
                    "股票池覆盖："
                    f"eligible_symbols={int(coverage.get('symbol_count') or 0)}, "
                    f"ready_symbols={coverage['ready_symbol_count']}, "
                    f"fresh_ready_symbols={coverage.get('fresh_ready_symbol_count', '-')}, "
                    f"bars={int(coverage.get('bar_count') or 0)}, "
                    f"scope={stock_scope}, quick_limit={quick_limit if stock_scope == 'quick' else '-'}"
                )
                print("[3/3] 使用生产信号链真实执行 vn.py Alpha101", flush=True)
                provider = VnpyAlphaSignalProvider(repository)
                provider.candidate_limit = limit
                run = {"strategy_code": strategy, "stock_scope": stock_scope,
                       "quick_limit": quick_limit}
                candidates = _validate_candidates(provider.candidates(run, as_of, {"stock"}))
            finally:
                repository.close_current_thread()
        elif repository is not None:
            repository.close_current_thread()
    if candidates is None:
        if not current_snapshot:
            raise RuntimeError(
                "历史 --as-of 不使用当前 AKShare 股票池回退，以免未来数据泄漏；"
                "请先准备该历史截面的 MySQL 行情"
            )
        candidates = _run_akshare_selection(
            strategy, as_of, stock_scope, quick_limit, limit, max_data_lag_days, allow_stale
        )
        market_source = "AKShare/东方财富"

    print(
        f"真实选股完成：strategy={strategy}, candidates={len(candidates)}, "
        f"market_source={market_source}, signal_source=vnpy_alpha101"
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
        description="MySQL 行情不足时按需获取 AKShare 真实日线，再运行 vn.py Alpha101（全程只读）"
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
        "--data-source", choices=("auto", "mysql", "akshare"), default="auto",
        help="默认先读 MySQL，不足时用 AKShare；可强制只用其中一条路径",
    )
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
            data_source=args.data_source,
            current_snapshot=args.as_of is None,
        )
    except Exception as exc:
        print(f"DEMO FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("MILESTONE DEMO OK: 已使用真实行情完成 vn.py Alpha101 选股")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
