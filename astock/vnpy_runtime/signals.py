"""基于已安装 vn.py 的 Alpha101 实现生成 A 股横截面交易信号。"""
from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any, Iterable

from .gateway import _normalize_vt_symbol


ALPHA101_PROFILES = {
    "short_term": ("alpha2", "alpha6", "alpha12", "alpha25", "alpha43"),
    "medium_term": ("alpha3", "alpha15", "alpha22", "alpha40", "alpha55"),
    "long_term": ("alpha19", "alpha25", "alpha39", "alpha52", "alpha60"),
}

REFERENCE_PROFILES = {
    "short_term": ("alpha1", "alpha7", "alpha17", "alpha30", "alpha34"),
    "medium_term": ("alpha4", "alpha16", "alpha28", "alpha31", "alpha41"),
    "long_term": ("alpha20", "alpha24", "alpha32", "alpha35", "alpha45"),
}


class VnpyAlphaSignalError(RuntimeError):
    """vn.py Alpha 信号无法安全生成。"""


def generate_alpha101_signals(
    bars: Iterable[dict[str, Any]],
    strategy_code: str,
    reference: bool = False,
) -> list[dict[str, Any]]:
    """使用 vn.py 的 Alpha101 表达式计算单个交易截面的股票排名。

    输入只允许包含决策截面及之前的日线；这里不训练会使用未来标签的模型，避免在
    盘前候选生成中产生数据泄漏。输出的 ``signal`` 可直接作为 vn.py
    ``EquityDemoStrategy`` 的多标的排序输入。
    """
    profile_map = REFERENCE_PROFILES if reference else ALPHA101_PROFILES
    feature_names = profile_map.get(strategy_code)
    if not feature_names:
        raise VnpyAlphaSignalError(f"不支持的 vn.py 策略代码：{strategy_code}")

    try:
        import polars as pl
        from vnpy.alpha.dataset.datasets.alpha_101 import Alpha101
        from vnpy.alpha.dataset.utility import calculate_by_expression
    except ImportError as exc:
        raise VnpyAlphaSignalError(
            "vn.py Alpha101 运行依赖不可用；请使用 Python 3.10+ 并安装本项目 requirements.txt"
        ) from exc

    normalized = _normalize_bars(bars)
    if not normalized:
        raise VnpyAlphaSignalError("没有可用于 vn.py Alpha101 的有效日线")

    data = pl.DataFrame(normalized).sort(["datetime", "vt_symbol"])
    if data["vt_symbol"].n_unique() < 3:
        raise VnpyAlphaSignalError("vn.py 横截面策略至少需要 3 个有效标的")

    start = str(data["datetime"].min())
    end = str(data["datetime"].max())
    dataset = Alpha101(data, (start, end), (start, end), (start, end))

    for name in feature_names:
        expression = dataset.feature_expressions[name]
        feature = calculate_by_expression(data, expression)
        data = data.with_columns(feature["data"].alias(name))

    as_of = data["datetime"].max()
    current = data.filter(pl.col("datetime") == as_of).select(
        ["vt_symbol", "close", *feature_names]
    ).to_dicts()
    return _rank_cross_section(current, feature_names, as_of)


def _normalize_bars(bars: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    required = ("open", "high", "low", "close", "volume")
    for raw in bars:
        try:
            values = {name: float(raw[name]) for name in required}
            if not all(isfinite(value) and value > 0 for value in values.values()):
                continue
            turnover = float(raw.get("turnover") or raw.get("amount") or 0)
            vwap = turnover / values["volume"] if turnover > 0 else values["close"]
            normalized.append({
                "datetime": _as_datetime(raw["trade_date"]),
                "vt_symbol": _normalize_vt_symbol(raw["ts_code"]),
                **values,
                "vwap": vwap,
            })
        except (KeyError, TypeError, ValueError):
            continue
    return normalized


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)


def _rank_cross_section(rows: list[dict[str, Any]], feature_names: tuple[str, ...],
                        as_of: datetime) -> list[dict[str, Any]]:
    scores: dict[str, list[float]] = {str(row["vt_symbol"]): [] for row in rows}
    for name in feature_names:
        valid = [(str(row["vt_symbol"]), float(row[name])) for row in rows
                 if row.get(name) is not None and isfinite(float(row[name]))]
        valid.sort(key=lambda item: item[1])
        if len(valid) < 3:
            continue
        denominator = len(valid) - 1
        for rank, (vt_symbol, _) in enumerate(valid):
            scores[vt_symbol].append(rank / denominator)

    output = []
    for row in rows:
        vt_symbol = str(row["vt_symbol"])
        values = scores[vt_symbol]
        if len(values) < 3:
            continue
        output.append({
            "vt_symbol": vt_symbol,
            "close": float(row["close"]),
            "signal": sum(values) / len(values),
            "factor_count": len(values),
            "as_of": as_of,
        })
    return sorted(output, key=lambda item: item["signal"], reverse=True)
