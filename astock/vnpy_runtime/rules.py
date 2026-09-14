"""第一阶段的只读股票选股规则。

规则只生成研究信号，不读取账户、不创建计划，也不提交订单。输入为已经按
``as_of`` 截断的日线，输出格式与 ``VnpyAlphaSignalProvider`` 兼容。
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Iterable


DEFAULT_RULE_STRATEGIES = {
    "trend_momentum": "趋势动量",
    "breakout_volume": "突破放量",
    "low_volatility": "低波动趋势",
}


def generate_rule_signals(bars: Iterable[dict[str, Any]], strategy_code: str) -> list[dict[str, Any]]:
    if strategy_code not in DEFAULT_RULE_STRATEGIES:
        raise ValueError(f"不支持的默认规则策略：{strategy_code}")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for raw in bars:
        try:
            symbol = str(raw["ts_code"])
            values = {name: float(raw[name]) for name in ("open", "high", "low", "close", "volume")}
            if not all(isfinite(value) and value > 0 for value in values.values()):
                continue
            grouped.setdefault(symbol, []).append({"ts_code": symbol, "trade_date": raw["trade_date"], **values})
        except (KeyError, TypeError, ValueError):
            continue

    raw_results = []
    for ts_code, rows in grouped.items():
        rows.sort(key=lambda row: str(row["trade_date"]))
        if strategy_code == "trend_momentum":
            result = _trend_momentum(ts_code, rows)
        elif strategy_code == "breakout_volume":
            result = _breakout_volume(ts_code, rows)
        else:
            result = _low_volatility(ts_code, rows)
        if result:
            raw_results.append(result)

    if len(raw_results) < 3:
        return []
    _rank_in_place(raw_results, "raw_score")
    return sorted(raw_results, key=lambda item: item["signal"], reverse=True)


def _trend_momentum(ts_code: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(rows) < 60:
        return None
    closes = [row["close"] for row in rows]
    latest = closes[-1]
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60
    momentum20 = latest / closes[-21] - 1
    momentum60 = latest / closes[-61] - 1
    if not (latest > ma20 > ma60 and momentum20 > 0 and momentum60 > 0):
        return None
    return _result(ts_code, latest, momentum20 + momentum60, "收盘价位于 MA20 上方且 MA20 高于 MA60，20/60 日动量为正", rows[-1]["trade_date"])


def _breakout_volume(ts_code: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(rows) < 21:
        return None
    latest = rows[-1]
    prior_high = max(row["high"] for row in rows[-21:-1])
    avg_volume = sum(row["volume"] for row in rows[-21:-1]) / 20
    breakout = latest["close"] / prior_high - 1
    volume_ratio = latest["volume"] / avg_volume if avg_volume else 0
    if breakout <= 0 or volume_ratio < 1:
        return None
    return _result(ts_code, latest["close"], breakout + min(volume_ratio, 5) / 10,
                   f"突破前 20 日高点，成交量为 20 日均量的 {volume_ratio:.2f} 倍", latest["trade_date"])


def _low_volatility(ts_code: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(rows) < 30:
        return None
    closes = [row["close"] for row in rows]
    returns = [closes[index] / closes[index - 1] - 1 for index in range(len(closes) - 20, len(closes))]
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / len(returns)
    volatility = variance ** 0.5
    momentum20 = closes[-1] / closes[-21] - 1
    ma20 = sum(closes[-20:]) / 20
    if momentum20 <= 0 or closes[-1] <= ma20:
        return None
    return _result(ts_code, closes[-1], momentum20 - volatility,
                   f"20 日动量为正且收盘价在 MA20 上方，20 日波动率 {volatility:.4f}", rows[-1]["trade_date"])


def _result(ts_code: str, close: float, score: float, reason: str, as_of: Any) -> dict[str, Any]:
    return {
        "vt_symbol": _to_vt_symbol(ts_code),
        "close": close,
        "raw_score": score,
        "reason": reason,
        "factor_count": 1,
        "as_of": as_of,
    }


def _rank_in_place(rows: list[dict[str, Any]], field: str) -> None:
    ordered = sorted(rows, key=lambda item: item[field])
    denominator = max(1, len(ordered) - 1)
    for rank, row in enumerate(ordered):
        row["signal"] = rank / denominator


def _to_vt_symbol(ts_code: str) -> str:
    symbol, exchange = ts_code.upper().rsplit(".", 1)
    exchange = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}.get(exchange, exchange)
    return f"{symbol}.{exchange}"
