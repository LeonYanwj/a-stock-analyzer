"""全市场因子输入的证券唯一性测试。"""
import unittest

try:
    import pandas as pd
except ImportError:
    pd = None

if pd is not None:
    from data.fetcher import deduplicate_fund_flow_rows
    from market_data_integrity import (MarketDataIntegrityError, ensure_unique_panel,
                                       one_row_per_ts_code)
    from screen import select_stock_scan_universe


@unittest.skipUnless(pd is not None, "当前解释器未安装 Pandas")
class MarketDataIntegrityTests(unittest.TestCase):
    def test_exact_duplicate_source_rows_are_collapsed_before_merge(self):
        source = pd.DataFrame([
            {"ts_code": "600000.SH", "pe_ttm": 8.2, "pb": 0.5},
            {"ts_code": "600000.SH", "pe_ttm": 8.2, "pb": 0.5},
            {"ts_code": "000001.SZ", "pe_ttm": 6.1, "pb": 0.7},
        ])

        result = one_row_per_ts_code(source, ["ts_code", "pe_ttm", "pb"], "市场快照")

        self.assertEqual(result["ts_code"].tolist(), ["600000.SH", "000001.SZ"])

    def test_conflicting_duplicate_source_rows_raise_source_specific_error(self):
        source = pd.DataFrame([
            {"ts_code": "600000.SH", "fund_net": 100.0},
            {"ts_code": "600000.SH", "fund_net": 200.0},
        ])

        with self.assertRaisesRegex(MarketDataIntegrityError, "资金流快照.*600000.SH"):
            one_row_per_ts_code(source, ["ts_code", "fund_net"], "资金流快照")

    def test_fund_flow_source_duplicates_keep_the_first_ranked_row(self):
        source = pd.DataFrame([
            {"ts_code": "600165.SH", "symbol": "600165", "fund_net": 100.0},
            {"ts_code": "600165.SH", "symbol": "600165", "fund_net": 200.0},
            {"ts_code": "000001.SZ", "symbol": "000001", "fund_net": 50.0},
        ])

        result = deduplicate_fund_flow_rows(source)

        self.assertEqual(result["ts_code"].tolist(), ["600165.SH", "000001.SZ"])
        self.assertEqual(result.loc[0, "fund_net"], 100.0)

    def test_factor_panel_rejects_duplicate_security_trade_date_pairs(self):
        panel = pd.DataFrame([
            {"ts_code": "600000.SH", "trade_date": "2026-08-15", "close": 10.0},
            {"ts_code": "600000.SH", "trade_date": "2026-08-15", "close": 10.0},
        ])

        with self.assertRaisesRegex(MarketDataIntegrityError, "因子输入日线.*600000.SH"):
            ensure_unique_panel(panel)

    def test_quick_scope_uses_top_amount_with_a_stable_code_tiebreaker(self):
        universe = pd.DataFrame([
            {"ts_code": "600000.SH", "name": "浦发银行"},
            {"ts_code": "000001.SZ", "name": "平安银行"},
            {"ts_code": "600001.SH", "name": "测试股份"},
        ])
        spot = pd.DataFrame([
            {"ts_code": "600000.SH", "amount": 100.0},
            {"ts_code": "000001.SZ", "amount": 200.0},
            {"ts_code": "600001.SH", "amount": 200.0},
        ])

        selected = select_stock_scan_universe(universe, spot, "quick", 2)

        self.assertEqual(selected["ts_code"].tolist(), ["000001.SZ", "600001.SH"])
