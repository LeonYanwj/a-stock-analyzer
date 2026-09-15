"""不连接数据库、不安装 vn.py 时验证第一里程碑 Demo 的防护逻辑。"""
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from examples.vnpy_milestone_demo import (
    _history_bars, _load_akshare_bars, _run_real_selection, _snapshot_symbols,
    _validate_candidates, _validate_coverage,
)


class VnpyMilestoneDemoTests(unittest.TestCase):
    def test_coverage_accepts_fresh_real_history(self):
        lag = _validate_coverage(
            {
                "symbol_count": 100,
                "ready_symbol_count": 80,
                "latest_trade_date": "2026-09-11",
            },
            cutoff=date(2026, 9, 13),
            max_data_lag_days=10,
            allow_stale=False,
        )
        self.assertEqual(lag, 2)

    def test_coverage_rejects_stale_or_insufficient_history(self):
        with self.assertRaisesRegex(RuntimeError, "滞后"):
            _validate_coverage(
                {"symbol_count": 100, "ready_symbol_count": 80,
                 "latest_trade_date": "2026-08-01"},
                cutoff=date(2026, 9, 13), max_data_lag_days=10, allow_stale=False,
            )
        with self.assertRaisesRegex(RuntimeError, "至少需要 3 只"):
            _validate_coverage(
                {"symbol_count": 100, "ready_symbol_count": 2,
                 "latest_trade_date": "2026-09-11"},
                cutoff=date(2026, 9, 13), max_data_lag_days=10, allow_stale=False,
            )

    def test_coverage_rejects_too_small_quick_pool(self):
        with self.assertRaisesRegex(RuntimeError, "至少需要 40 只"):
            _validate_coverage(
                {"symbol_count": 50, "ready_symbol_count": 20,
                 "latest_trade_date": "2026-09-11"},
                cutoff=date(2026, 9, 13), max_data_lag_days=10,
                allow_stale=False, required_symbols=40,
            )

    def test_coverage_counts_only_fresh_ready_symbols(self):
        with self.assertRaisesRegex(RuntimeError, "新鲜历史日线"):
            _validate_coverage(
                {"symbol_count": 100, "ready_symbol_count": 100,
                 "fresh_ready_symbol_count": 20, "latest_trade_date": "2026-09-11"},
                cutoff=date(2026, 9, 13), max_data_lag_days=10,
                allow_stale=False, required_symbols=80,
            )

    def test_snapshot_filters_st_and_ranks_current_main_board(self):
        snapshot = [
            {"代码": "600000", "名称": "浦发银行", "成交额": 200},
            {"代码": "000001", "名称": "平安银行", "成交额": 300},
            {"代码": "300001", "名称": "创业板股票", "成交额": 999},
            {"代码": "002001", "名称": "*ST测试", "成交额": 500},
        ]
        self.assertEqual(_snapshot_symbols(snapshot, 2), ["000001", "600000"])
        with self.assertRaisesRegex(RuntimeError, "不能按完整股票池验收"):
            _snapshot_symbols(snapshot, 3)

    def test_history_rejects_duplicates_and_future_bars(self):
        row = {"日期": "2026-09-11", "开盘": 10, "最高": 11, "最低": 9,
               "收盘": 10.5, "成交量": 100, "成交额": 105000}
        future = dict(row, 日期="2026-09-14")
        bars = _history_bars("600000", [row, future], date(2026, 9, 13))
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["volume"], 10000)
        self.assertEqual(bars[0]["ts_code"], "600000.SH")
        with self.assertRaisesRegex(RuntimeError, "重复交易日"):
            _history_bars("600000", [row, row], date(2026, 9, 13))

    def test_akshare_empty_mysql_route_uses_real_api_rows_without_db(self):
        cutoff = date(2026, 9, 13)
        symbols = ("600000", "000001", "002001")
        snapshot = [{"代码": symbol, "名称": "正常股票", "成交额": 300 - rank}
                    for rank, symbol in enumerate(symbols)]
        history = [{"日期": (cutoff - timedelta(days=day)).isoformat(),
                    "开盘": 10, "最高": 11, "最低": 9, "收盘": 10,
                    "成交量": 100, "成交额": 100000}
                   for day in range(269, -1, -1)]

        class Frame:
            def __init__(self, rows):
                self.rows = rows

            def to_dict(self, orient):
                if orient != "records":
                    raise AssertionError(f"unexpected orient: {orient}")
                return self.rows

        fake_akshare = SimpleNamespace(
            stock_zh_a_spot_em=lambda: Frame(snapshot),
            stock_zh_a_hist=lambda **kwargs: Frame(history),
        )
        with patch.dict("sys.modules", {"akshare": fake_akshare}):
            bars, coverage = _load_akshare_bars(cutoff, 3, 10, False)
        self.assertEqual(coverage["ready"], 3)
        self.assertEqual(coverage["latest_trade_date"], cutoff)
        self.assertEqual(len(bars), 810)
        self.assertTrue(all(bar["trade_date"] <= cutoff for bar in bars))

    def test_historical_as_of_never_uses_today_snapshot(self):
        with patch("examples.vnpy_milestone_demo._run_akshare_selection") as fetch:
            with self.assertRaisesRegex(RuntimeError, "未来数据泄漏"):
                _run_real_selection(
                    "medium_term", datetime(2026, 1, 1), "quick", 50, 10, 10, False,
                    data_source="akshare", current_snapshot=False,
                )
            fetch.assert_not_called()

    def test_candidates_must_be_ranked_and_from_vnpy_alpha101(self):
        rows = _validate_candidates([
            {"score": 0.9, "data_source": "vnpy_alpha101"},
            {"score": 0.4, "data_source": "vnpy_alpha101"},
        ])
        self.assertEqual(len(rows), 2)
        with self.assertRaisesRegex(RuntimeError, "并非全部来自"):
            _validate_candidates([{"score": 0.9, "data_source": "custom_rule"}])

    def test_candidates_reject_empty_or_unranked_output(self):
        with self.assertRaisesRegex(RuntimeError, "没有从真实行情生成候选"):
            _validate_candidates([])
        with self.assertRaisesRegex(RuntimeError, "降序"):
            _validate_candidates([
                {"score": 0.2, "data_source": "vnpy_alpha101"},
                {"score": 0.8, "data_source": "vnpy_alpha101"},
            ])


if __name__ == "__main__":
    unittest.main()
