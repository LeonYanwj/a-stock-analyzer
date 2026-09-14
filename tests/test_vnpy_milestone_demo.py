"""不连接数据库、不安装 vn.py 时验证第一里程碑 Demo 的防护逻辑。"""
import unittest
from datetime import date

from examples.vnpy_milestone_demo import _validate_candidates, _validate_coverage


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
        with self.assertRaisesRegex(RuntimeError, "不足 3 只"):
            _validate_coverage(
                {"symbol_count": 100, "ready_symbol_count": 2,
                 "latest_trade_date": "2026-09-11"},
                cutoff=date(2026, 9, 13), max_data_lag_days=10, allow_stale=False,
            )

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
