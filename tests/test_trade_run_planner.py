import unittest
from datetime import datetime

from trade_run.planner import TradeRunPlanner
from trade_run.repository import SqliteTradeRunRepository
from trade_run.service import TradeRunService


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
                                           ["stock", "etf"], signal_source="legacy")
        self.service.start_run(self.run["run_id"])
        self.as_of = datetime(2026, 8, 13, 8, 50)

    def row(self, code, asset_type="stock"):
        return {"ts_code": code, "asset_type": asset_type, "side": "buy",
                "reference_price": 10, "reason": "截面验证", "data_status": "delayed",
                "data_source": "fixture", "market_time": self.as_of}

    def test_primary_shadow_overlap_and_idempotent_window(self):
        planner = TradeRunPlanner(self.service, {
            "legacy": FixedProvider([self.row("600000.SH"), self.row("510300.SH", "etf")]),
            "new": FixedProvider([self.row("600000.SH"), self.row("600519.SH")]),
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

        planner = TradeRunPlanner(self.service, {"legacy": BrokenProvider(), "new": BrokenProvider()})
        with self.assertRaises(Exception):
            planner.generate(self.run["run_id"], "midday", self.as_of)
        events = self.service.repo._many(self.service.repo.conn.execute("SELECT * FROM risk_event"))
        self.assertEqual(events[0]["event_code"], "PLAN_GENERATION_FAILED")

    def test_empty_window_is_idempotent(self):
        planner = TradeRunPlanner(self.service, {
            "legacy": FixedProvider([]), "new": FixedProvider([]),
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
                    captured.append(params[0])
                    class Cursor:
                        def fetchall(self):
                            return []
                    return Cursor()
            conn = Connection()

        from trade_run.signal_providers import RuleSignalProvider
        provider = RuleSignalProvider(QueryRepo())
        provider._daily_candidates("market_daily", "stock", self.as_of, 10)
        self.assertEqual(captured, ["2026-08-12"])
