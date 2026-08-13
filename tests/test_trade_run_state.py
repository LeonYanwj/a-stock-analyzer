import unittest

from trade_run.models import TradeRunError
from trade_run.repository import SqliteTradeRunRepository
from trade_run.service import TradeRunService


class TradeRunStateTests(unittest.TestCase):
    def setUp(self):
        self.service = TradeRunService(SqliteTradeRunRepository())
        self.service.repo.initialize()

    def create(self, strategy="short_term"):
        return self.service.create_run("验证实例", strategy, 100000, 0.8, ["stock", "etf"])

    def test_create_defaults_to_draft_and_freezes_version(self):
        run = self.create()
        self.assertEqual(run["status"], "draft")
        self.assertEqual(run["initial_capital"], 100000)
        self.assertEqual(run["frozen_config"]["strategy_version"], 1)

    def test_pause_requires_explicit_restart(self):
        run = self.create()
        started = self.service.start_run(run["run_id"])
        self.assertEqual(started["status"], "running")
        paused = self.service.stop_run(run["run_id"], "pause")
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(self.service.get_run(run["run_id"])["status"], "paused")
        self.assertEqual(self.service.start_run(run["run_id"])["status"], "running")

    def test_same_strategy_allows_only_one_running_run(self):
        first = self.create()
        second = self.create()
        self.service.start_run(first["run_id"])
        with self.assertRaises(TradeRunError) as ctx:
            self.service.start_run(second["run_id"])
        self.assertEqual(ctx.exception.code, "STRATEGY_RUN_ALREADY_ACTIVE")

    def test_deleted_run_cannot_start_and_history_remains(self):
        run = self.create()
        deleted = self.service.delete_run(run["run_id"])
        self.assertEqual(deleted["status"], "deleted")
        self.assertIsNotNone(deleted["deleted_at"])
        with self.assertRaises(TradeRunError) as ctx:
            self.service.start_run(run["run_id"])
        self.assertEqual(ctx.exception.code, "TRADE_RUN_DELETED")
        self.assertEqual(len(self.service.list_runs(include_deleted=True)), 1)

    def test_ended_run_cannot_restart(self):
        run = self.create()
        self.service.start_run(run["run_id"])
        self.service.stop_run(run["run_id"], "end")
        with self.assertRaises(TradeRunError) as ctx:
            self.service.start_run(run["run_id"])
        self.assertEqual(ctx.exception.code, "INVALID_RUN_TRANSITION")
