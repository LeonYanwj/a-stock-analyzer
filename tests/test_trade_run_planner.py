import unittest
from datetime import datetime

from astock.trade_run.planner import TradeRunPlanner
from astock.trade_run.repository import SqliteTradeRunRepository
from astock.trade_run.service import TradeRunService


class FixedProvider:
    def __init__(self, rows):
        self.rows = rows

    def candidates(self, run, as_of, asset_types):
        return list(self.rows)


class TradeRunPlannerTests(unittest.TestCase):
    def setUp(self):
        repo = SqliteTradeRunRepository()
        repo.initialize()
        self.service = TradeRunService(repo)
        self.run = self.service.create_run("计划验证", "short_term", 100000, .8,
                                           ["stock", "etf"], signal_source="vnpy")
        self.service.start_run(self.run["run_id"])
        self.as_of = datetime(2026, 8, 13, 8, 50)

    def row(self, code, asset_type="stock"):
        return {"ts_code": code, "asset_type": asset_type, "side": "buy",
                "reference_price": 10, "reason": "截面验证", "data_status": "delayed",
                "data_source": "fixture", "market_time": self.as_of}

    def test_primary_shadow_overlap_and_idempotent_window(self):
        planner = TradeRunPlanner(self.service, {
            "vnpy": FixedProvider([self.row("600000.SH"), self.row("510300.SH", "etf")]),
            "vnpy_reference": FixedProvider([self.row("600000.SH"), self.row("600519.SH")]),
        })
        result = planner.generate(self.run["run_id"], "pre_market", self.as_of)
        self.assertFalse(result["idempotent"])
        plans = self.service.list_plans(self.run["run_id"])
        self.assertEqual(len(plans), 4)
        self.assertTrue(all(p["as_of"] == self.as_of.isoformat() for p in plans))
        self.assertTrue(all(p["execution_confirmation_required"] for p in plans))
        comparison = self.service.comparisons(self.run["run_id"])
        self.assertEqual(len(comparison["overlap"]), 1)
        self.assertEqual(len(comparison["primary_only"]), 1)
        self.assertEqual(len(comparison["shadow_only"]), 1)
        primary_overlap = comparison["overlap"][0]["primary_plan_id"]
        fill = self.service.record_fill(self.run["run_id"], idempotency_key="overlap-fill",
                                        plan_id=primary_overlap, ts_code="600000.SH", side="buy",
                                        qty=1000, price=10, fee=5,
                                        executed_at="2026-08-13T09:35:00", source="manual",
                                        broker_quote_confirmed=True,
                                        quote_checked_at="2026-08-13T09:34:00")
        comparison = self.service.comparisons(self.run["run_id"])
        self.assertEqual(comparison["overlap"][0]["mirrored_fill_id"], fill["fill"]["fill_id"])
        shadow_overlap = comparison["overlap"][0]["shadow_plan_id"]
        with self.assertRaises(Exception) as ctx:
            self.service.record_fill(self.run["run_id"], idempotency_key="shadow-fill",
                                     plan_id=shadow_overlap, ts_code="600000.SH", side="buy",
                                     qty=1000, price=10, fee=5,
                                     executed_at="2026-08-13T09:36:00", source="manual",
                                     broker_quote_confirmed=True,
                                     quote_checked_at="2026-08-13T09:35:30")
        self.assertEqual(ctx.exception.code, "SHADOW_PLAN_NOT_EXECUTABLE")
        again = planner.generate(self.run["run_id"], "pre_market", self.as_of)
        self.assertTrue(again["idempotent"])
        self.assertEqual(len(self.service.list_plans(self.run["run_id"])), 4)
        next_day = planner.generate(self.run["run_id"], "pre_market", self.as_of.replace(day=14))
        self.assertFalse(next_day["idempotent"])
        self.assertEqual(len(self.service.list_plans(self.run["run_id"])), 8)

    def test_generation_failure_is_a_risk_event(self):
        class BrokenProvider:
            def candidates(self, *args):
                raise RuntimeError("data unavailable")

        planner = TradeRunPlanner(self.service, {"vnpy": BrokenProvider(), "vnpy_reference": BrokenProvider()})
        with self.assertRaises(Exception):
            planner.generate(self.run["run_id"], "midday", self.as_of)
        events = self.service.repo._many(self.service.repo.conn.execute("SELECT * FROM risk_event"))
        self.assertEqual(events[0]["event_code"], "PLAN_GENERATION_FAILED")

    def test_failed_window_can_be_retried_after_fix(self):
        class BrokenProvider:
            def candidates(self, *args):
                raise RuntimeError("data unavailable")

        with self.assertRaises(Exception):
            TradeRunPlanner(self.service, {"vnpy": BrokenProvider(), "vnpy_reference": BrokenProvider()}).generate(
                self.run["run_id"], "midday", self.as_of
            )
        recovered = TradeRunPlanner(self.service, {
            "vnpy": FixedProvider([self.row("600000.SH")]),
            "vnpy_reference": FixedProvider([self.row("600519.SH")]),
        }).generate(self.run["run_id"], "midday", self.as_of)
        self.assertFalse(recovered["idempotent"])
        self.assertEqual(len(self.service.list_plans(self.run["run_id"])), 2)

    def test_pause_during_generation_discards_candidates(self):
        service = self.service
        candidate = self.row("600000.SH")

        class PausingProvider:
            def candidates(self, *args):
                service.stop_run(self.run_id, "pause", "验收暂停")
                return [candidate]

            def __init__(self, run_id):
                self.run_id = run_id

        planner = TradeRunPlanner(self.service, {
            "vnpy": PausingProvider(self.run["run_id"]), "vnpy_reference": FixedProvider([self.row("600519.SH")]),
        })
        with self.assertRaises(Exception) as ctx:
            planner.generate(self.run["run_id"], "midday", self.as_of)
        self.assertEqual(ctx.exception.code, "PLAN_GENERATION_CANCELLED")
        self.assertEqual(self.service.list_plans(self.run["run_id"]), [])

    def test_empty_window_is_idempotent(self):
        planner = TradeRunPlanner(self.service, {
            "vnpy": FixedProvider([]), "vnpy_reference": FixedProvider([]),
        })
        first = planner.generate(self.run["run_id"], "pre_market", self.as_of)
        second = planner.generate(self.run["run_id"], "pre_market", self.as_of)
        self.assertEqual(first["primary_plan_count"], 0)
        self.assertTrue(second["idempotent"])

    def test_rule_provider_daily_query_excludes_same_day_bar(self):
        captured = []

        class QueryRepo:
            class Connection:
                def execute(self, sql, params):
                    captured.append((sql, params))
                    class Cursor:
                        def fetchall(self):
                            return []
                    return Cursor()
            conn = Connection()

        from astock.trade_run.signal_providers import RuleSignalProvider
        provider = RuleSignalProvider(QueryRepo())
        provider._daily_candidates("market_daily", "stock", self.as_of, 10)
        sql, params = captured[0]
        self.assertNotIn("LIKE '600%'", sql)
        self.assertEqual(params, ("600%", "601%", "603%", "605%", "000%", "001%", "002%", "2026-08-12"))

    def test_vnpy_provider_uses_prior_day_bars_and_converts_vt_symbols(self):
        captured = []

        class QueryRepo:
            class Connection:
                def execute(self, sql, params):
                    captured.append((sql, params))

                    class Cursor:
                        def fetchall(self):
                            return [{
                                "ts_code": "600000.SH", "trade_date": "2026-08-12",
                                "open": 10, "high": 11, "low": 9, "close": 10.5,
                                "vol": 1000, "amount": 10500,
                            }]
                    return Cursor()
            conn = Connection()

        from unittest.mock import patch
        from astock.trade_run.signal_providers import VnpyAlphaSignalProvider

        provider = VnpyAlphaSignalProvider(QueryRepo())
        with patch("astock.vnpy_runtime.signals.generate_alpha101_signals", return_value=[{
            "vt_symbol": "600000.SSE", "close": 10.5, "signal": 0.8, "factor_count": 5,
        }]):
            rows = provider._stock_candidates({"strategy_code": "short_term"}, self.as_of)

        sql, params = captured[0]
        self.assertIn("d.trade_date<=?", sql)
        self.assertEqual(params[0], "2026-08-12")
        self.assertEqual(rows[0]["ts_code"], "600000.SH")
        self.assertEqual(rows[0]["data_source"], "vnpy_alpha101")

    def test_vnpy_provider_uses_whitelisted_etf_bars(self):
        captured = []

        class QueryRepo:
            class Connection:
                def execute(self, sql, params):
                    captured.append((sql, params))

                    class Cursor:
                        def fetchall(self):
                            return [{
                                "ts_code": "510300.SH", "trade_date": "2026-08-12",
                                "open": 4, "high": 4.1, "low": 3.9, "close": 4.05,
                                "vol": 1000, "amount": 4050,
                            }]
                    return Cursor()
            conn = Connection()

        from unittest.mock import patch
        from astock.trade_run.signal_providers import VnpyAlphaSignalProvider

        provider = VnpyAlphaSignalProvider(QueryRepo())
        with patch("astock.vnpy_runtime.signals.generate_alpha101_signals", return_value=[{
            "vt_symbol": "510300.SSE", "close": 4.05, "signal": 0.8, "factor_count": 5,
        }]):
            rows = provider._etf_candidates({"strategy_code": "medium_term"}, self.as_of)

        sql, params = captured[0]
        self.assertIn("market_etf_basic", sql)
        self.assertIn("b.whitelist=?", sql)
        self.assertEqual(params[:3], ("2026-08-12", "active", 1))
        self.assertEqual(rows[0]["asset_type"], "etf")
        self.assertEqual(rows[0]["ts_code"], "510300.SH")
