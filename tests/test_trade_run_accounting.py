import unittest

from trade_run.models import TradeRunError
from trade_run.repository import SqliteTradeRunRepository
from trade_run.service import TradeRunService


class TradeRunAccountingTests(unittest.TestCase):
    def setUp(self):
        self.service = TradeRunService(SqliteTradeRunRepository())
        self.service.repo.initialize()
        self.run = self.service.create_run("短线验证", "short_term", 100000, 0.8, ["stock"])
        self.service.start_run(self.run["run_id"])

    def plan(self, **changes):
        data = dict(ts_code="600000.SH", asset_type="stock", side="buy", suggested_qty=1000,
                    reference_price=10, data_status="delayed", blocked_reason="免费行情非实时")
        data.update(changes)
        return self.service.create_plan(self.run["run_id"], **data)

    def fill(self, **changes):
        data = dict(idempotency_key="fill-1", ts_code="600000.SH", side="buy", qty=1000,
                    price=10, fee=5, executed_at="2026-08-13T09:35:00", source="manual")
        data.update(changes)
        return self.service.record_fill(self.run["run_id"], **data)

    def test_plan_does_not_change_cash_or_positions(self):
        before = self.service.dashboard(self.run["run_id"])
        plan = self.plan()
        after = self.service.dashboard(self.run["run_id"])
        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(before["cash"], after["cash"])
        self.assertEqual(after["positions"], [])

    def test_buy_fill_updates_only_actual_fill_and_is_idempotent(self):
        result = self.fill()
        self.assertFalse(result["idempotent"])
        dash = self.service.dashboard(self.run["run_id"])
        self.assertEqual(dash["cash"], 89995.0)
        self.assertEqual(dash["positions"][0]["qty"], 1000)
        repeated = self.fill()
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(self.service.dashboard(self.run["run_id"])["cash"], 89995.0)

    def test_cash_and_position_constraints_are_enforced(self):
        with self.assertRaises(TradeRunError) as ctx:
            self.fill(qty=101)
        self.assertEqual(ctx.exception.code, "INVALID_QTY")
        with self.assertRaises(TradeRunError) as ctx:
            self.fill(qty=20000, price=10)
        self.assertEqual(ctx.exception.code, "INSUFFICIENT_CASH")
        self.fill()
        with self.assertRaises(TradeRunError) as ctx:
            self.fill(idempotency_key="sell-1", side="sell", qty=1100, price=11)
        self.assertEqual(ctx.exception.code, "INSUFFICIENT_POSITION")
        with self.assertRaises(TradeRunError) as ctx:
            self.fill(idempotency_key="sell-2", side="sell", qty=1000, price=11)
        self.assertEqual(ctx.exception.code, "T1_SELL_RESTRICTED")

    def test_frozen_total_position_limit_is_enforced(self):
        with self.assertRaises(TradeRunError) as ctx:
            self.fill(qty=8100, price=10)
        self.assertEqual(ctx.exception.code, "MAX_POSITION_EXCEEDED")

    def test_next_day_sell_updates_cash_and_realized_pnl(self):
        self.fill()
        self.fill(idempotency_key="sell-1", side="sell", qty=1000, price=11,
                  fee=5, executed_at="2026-08-14T10:00:00")
        dash = self.service.dashboard(self.run["run_id"])
        self.assertEqual(dash["cash"], 100990.0)
        self.assertEqual(dash["positions"], [])

    def test_fill_can_be_linked_to_matching_plan(self):
        plan = self.plan(blocked_reason=None, data_status="fresh", suggested_qty=2000)
        result = self.fill(plan_id=plan["plan_id"], qty=1000)
        self.assertEqual(result["fill"]["plan_id"], plan["plan_id"])
        self.assertEqual(self.service.get_plan(self.run["run_id"], plan["plan_id"])["status"], "partially_filled")
        self.fill(idempotency_key="fill-2", plan_id=plan["plan_id"], qty=1000)
        self.assertEqual(self.service.get_plan(self.run["run_id"], plan["plan_id"])["status"], "triggered")

    def test_plan_rejects_fill_above_remaining_quantity(self):
        plan = self.plan(blocked_reason=None, data_status="fresh")
        with self.assertRaises(TradeRunError) as ctx:
            self.fill(plan_id=plan["plan_id"], qty=1100)
        self.assertEqual(ctx.exception.code, "PLAN_QTY_EXCEEDED")

    def test_write_paths_request_account_lock(self):
        class LockTrackingRepo(SqliteTradeRunRepository):
            def __init__(self):
                super().__init__()
                self.lock_requests = 0

            def get_run(self, run_id, include_deleted=True, for_update=False):
                self.lock_requests += int(for_update)
                return super().get_run(run_id, include_deleted, for_update)

        repo = LockTrackingRepo()
        repo.initialize()
        service = TradeRunService(repo)
        run = service.create_run("锁验证", "short_term", 100000, 0.8, ["stock"])
        service.start_run(run["run_id"])
        service.record_fill(run["run_id"], idempotency_key="lock-fill", ts_code="600000.SH", side="buy", qty=1000, price=10, fee=5, executed_at="2026-08-13T09:35:00", asset_type="stock")
        self.assertGreaterEqual(repo.lock_requests, 2)

    def test_rebuy_after_flat_position_starts_new_open_date(self):
        self.fill()
        self.fill(idempotency_key="flat-sell", side="sell", qty=1000, price=10,
                  fee=5, executed_at="2026-08-14T10:00:00")
        self.fill(idempotency_key="rebuy", qty=1000, price=9,
                  fee=5, executed_at="2026-08-15T10:00:00")
        position = self.service.positions(self.run["run_id"])[0]
        self.assertEqual(position["open_date"], "2026-08-15")

    def test_blocked_plan_cannot_be_recorded_as_fill(self):
        plan = self.plan()
        with self.assertRaises(TradeRunError) as ctx:
            self.fill(plan_id=plan["plan_id"])
        self.assertEqual(ctx.exception.code, "PLAN_NOT_EXECUTABLE")
