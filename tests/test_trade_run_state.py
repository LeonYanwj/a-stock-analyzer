import threading
import unittest

from trade_run.models import TradeRunError
from trade_run.repository import SqliteTradeRunRepository, _MySqlConnectionAdapter
from trade_run.service import TradeRunService


class _FakeMySqlCursor:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=()):
        self.connection.executed_on_threads.append(threading.get_ident())


class _FakeMySqlConnection:
    def __init__(self):
        self.executed_on_threads = []
        self.ping_reconnect_values = []

    def ping(self, reconnect=False):
        self.ping_reconnect_values.append(reconnect)

    def cursor(self):
        return _FakeMySqlCursor(self)


class MySqlConnectionAdapterTests(unittest.TestCase):
    def test_each_worker_thread_gets_its_own_reconnectable_connection(self):
        connections = []
        connections_lock = threading.Lock()
        start = threading.Barrier(2)

        def create_connection():
            connection = _FakeMySqlConnection()
            with connections_lock:
                connections.append(connection)
            return connection

        adapter = _MySqlConnectionAdapter(connection_factory=create_connection)

        def run_query():
            start.wait(timeout=2)
            return adapter.execute("SELECT 1").connection

        results = []
        errors = []

        def collect():
            try:
                results.append(run_query())
            except Exception as exc:  # pragma: no cover - assertion below reports it
                errors.append(exc)

        threads = [threading.Thread(target=collect) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        self.assertFalse(errors)
        self.assertEqual(2, len(results))
        self.assertEqual(2, len({id(connection) for connection in results}))
        self.assertEqual(2, len(connections))
        self.assertTrue(all(connection.ping_reconnect_values == [True] for connection in connections))


class TradeRunStateTests(unittest.TestCase):
    def setUp(self):
        self.service = TradeRunService(SqliteTradeRunRepository())
        self.service.repo.initialize()

    def create(self, strategy="short_term"):
        return self.service.create_run("验证实例", strategy, 100000, 0.8, ["stock", "etf"], signal_source="legacy")

    def test_create_defaults_to_draft_and_freezes_version(self):
        run = self.create()
        self.assertEqual(run["status"], "draft")
        self.assertEqual(run["initial_capital"], 100000)
        self.assertEqual(run["frozen_config"]["strategy_version"], 1)

    def test_primary_signal_source_is_required_and_shadow_is_automatic(self):
        with self.assertRaises(TradeRunError) as ctx:
            self.service.create_run("缺少体系", "short_term", 100000, 0.8, ["stock"])
        self.assertEqual(ctx.exception.code, "SIGNAL_SOURCE_REQUIRED")
        run = self.service.create_run("新体系", "short_term", 100000, 0.8, ["stock"], signal_source="new")
        self.assertEqual(run["primary_signal_source"], "new")
        self.assertEqual(run["shadow_signal_source"], "legacy")

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
