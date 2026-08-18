"""ETF 历史日线初始化的领域测试。

这些测试只覆盖参数规范化和后台任务编排，不访问 MySQL、AKShare 或真实缓存目录。
"""
import unittest
from datetime import date

from api.etf_history import (build_history_window, normalize_etf_code,
                             run_etf_history_sync)


class _ProgressRecorder:
    def __init__(self):
        self.reports = []

    def report(self, progress, message):
        self.reports.append((progress, message))


class _Fetcher:
    def __init__(self):
        self.calls = []

    def get_etf_daily(self, ts_code, start_date, end_date):
        self.calls.append((ts_code, start_date, end_date))
        return [object(), object(), object()]


class EtfHistoryTests(unittest.TestCase):
    def test_normalizes_sh_and_sz_etf_codes(self):
        self.assertEqual(normalize_etf_code("510300.sh"), "510300.SH")
        self.assertEqual(normalize_etf_code("159915.SZ"), "159915.SZ")
        with self.assertRaisesRegex(ValueError, "ETF 代码"):
            normalize_etf_code("510300")

    def test_default_history_window_covers_ninety_calendar_days(self):
        start, end = build_history_window(today=date(2026, 8, 18))

        self.assertEqual(start, date(2026, 5, 20))
        self.assertEqual(end, date(2026, 8, 18))

    def test_history_window_rejects_reversed_dates(self):
        with self.assertRaisesRegex(ValueError, "开始日期"):
            build_history_window(date(2026, 8, 19), date(2026, 8, 18))

    def test_background_sync_uses_the_registered_code_and_reports_progress(self):
        task = _ProgressRecorder()
        fetcher = _Fetcher()

        result = run_etf_history_sync(
            task, "510300.SH", date(2026, 5, 20), date(2026, 8, 18),
            fetcher_factory=lambda: fetcher,
        )

        self.assertEqual(fetcher.calls, [("510300.SH", "20260520", "20260818")])
        self.assertEqual(result, {
            "ts_code": "510300.SH",
            "start_date": "2026-05-20",
            "end_date": "2026-08-18",
            "fetched_rows": 3,
        })
        self.assertEqual(task.reports[0][0], 5)
        self.assertEqual(task.reports[-1][0], 95)
