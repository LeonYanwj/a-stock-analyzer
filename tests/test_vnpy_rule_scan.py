"""第一阶段只读规则扫描测试。"""
import unittest
from datetime import date, timedelta

from astock.vnpy_runtime.rules import generate_rule_signals
from astock.trade_run.signal_providers import _apply_stock_scope


def _bars(code, start=10.0, count=65, volume=1000):
    rows = []
    day = date(2026, 1, 1)
    for index in range(count):
        close = start + index * 0.1
        rows.append({
            "ts_code": code, "trade_date": (day + timedelta(days=index)).isoformat(),
            "open": close - .05, "high": close + .1, "low": close - .1,
            "close": close, "volume": volume, "amount": close * volume,
        })
    return rows


class VnpyRuleScanTests(unittest.TestCase):
    def test_trend_momentum_returns_ranked_candidates(self):
        bars = _bars("600000.SH", 10) + _bars("600001.SH", 20) + _bars("600002.SH", 30)
        result = generate_rule_signals(bars, "trend_momentum")
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["vt_symbol"], "600000.SSE")
        self.assertGreaterEqual(result[0]["signal"], result[-1]["signal"])
        self.assertIn("MA20", result[0]["reason"])

    def test_quick_scope_keeps_latest_highest_amount_stocks(self):
        bars = _bars("600000.SH", volume=100) + _bars("600001.SH", volume=300) + _bars("600002.SH", volume=200)
        result = _apply_stock_scope(bars, {"stock_scope": "quick", "quick_limit": 2})
        self.assertEqual({row["ts_code"] for row in result}, {"600001.SH", "600002.SH"})

    def test_full_scope_keeps_all_stocks(self):
        bars = _bars("600000.SH") + _bars("600001.SH")
        self.assertEqual(_apply_stock_scope(bars, {"stock_scope": "full"}), bars)


if __name__ == "__main__":
    unittest.main()
