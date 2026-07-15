"""每日全市场评级与模拟盘退出规则测试。"""
import unittest
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from financial_risk import assess_financial_risk
from rating_rules import rating_exit_reason
from selector import score
from universe import filter_main_board


class FinancialRiskTests(unittest.TestCase):
    def test_loss_and_profit_drop_is_high_risk(self):
        result = assess_financial_risk({
            "net_profit": -10_000_000,
            "net_profit_yoy": -45,
            "revenue_yoy": 5,
            "roe": -2,
            "prev_roe": 1,
            "debt_ratio": 50,
            "industry": "软件服务",
        })
        self.assertFalse(result["eligible"])
        self.assertEqual(result["financial_risk_level"], "high")
        self.assertIn("LOSS_AND_PROFIT_DROP", result["financial_risk_flags"])

    def test_financial_industry_is_exempt_from_debt_ratio_rule(self):
        result = assess_financial_risk({
            "net_profit": 100,
            "net_profit_yoy": 10,
            "revenue_yoy": 8,
            "roe": 12,
            "prev_roe": 10,
            "debt_ratio": 92,
            "industry": "银行",
        })
        self.assertTrue(result["eligible"])
        self.assertEqual(result["financial_risk_level"], "low")


class UniverseTests(unittest.TestCase):
    def test_excludes_non_main_board_st_and_recent_listing(self):
        old = (datetime.now() - timedelta(days=800)).strftime("%Y%m%d")
        recent = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        stocks = pd.DataFrame([
            {"symbol": "600001", "ts_code": "600001.SH", "name": "正常股份", "list_date": old},
            {"symbol": "300001", "ts_code": "300001.SZ", "name": "创业样本", "list_date": old},
            {"symbol": "600002", "ts_code": "600002.SH", "name": "ST样本", "list_date": old},
            {"symbol": "002001", "ts_code": "002001.SZ", "name": "次新样本", "list_date": recent},
        ])
        result = filter_main_board(stocks, min_list_days=365)
        self.assertEqual(result["ts_code"].tolist(), ["600001.SH"])


class SelectorTests(unittest.TestCase):
    def test_rejects_rows_with_low_weight_coverage(self):
        factors = pd.DataFrame({
            "important": [1.0, 2.0, np.nan],
            "minor": [1.0, 2.0, 3.0],
        }, index=["a", "b", "c"])
        result = score(
            factors, weights={"important": 9.0, "minor": 1.0},
            min_valid_factors=1, min_weight_coverage=0.70)
        self.assertTrue(pd.isna(result.loc["c", "score"]))
        self.assertAlmostEqual(result.loc["c", "weight_coverage"], 0.1)


class RatingExitTests(unittest.TestCase):
    def test_single_weak_day_does_not_exit(self):
        current = {"grade": "B", "trend_state": "weak", "financial_risk_level": "low"}
        previous = {"trend_state": "good"}
        self.assertIsNone(rating_exit_reason(current, previous))

    def test_two_weak_days_exit(self):
        current = {"grade": "B", "trend_state": "weak", "financial_risk_level": "low"}
        previous = {"trend_state": "bad"}
        self.assertEqual(rating_exit_reason(current, previous), "TREND_WEAK_2D")

    def test_grade_d_exits_immediately(self):
        current = {"grade": "D", "trend_state": "good", "financial_risk_level": "low"}
        self.assertEqual(rating_exit_reason(current), "GRADE_D")

    def test_hard_eligibility_risk_exits_immediately(self):
        eligibility = {"eligible": False, "financial_risk_flags": ["ST_OR_DELIST_RISK"]}
        self.assertEqual(
            rating_exit_reason(None, eligibility=eligibility),
            "ST_OR_DELIST_RISK")


if __name__ == "__main__":
    unittest.main()
