"""市场扫描领域测试。

市场扫描只产生可解释的候选池；它不能创建交易计划或修改交易实例账务。
这些测试不依赖 FastAPI、MySQL 或外部行情。
"""
import json
import unittest
from datetime import datetime

from trade_run.market_scan import (get_market_scan_task, list_market_scan_tasks,
                                   run_market_scan, submit_market_scan)
from api.tasks import Task
from trade_run.models import TradeRunError


class _ProgressRecorder:
    def __init__(self):
        self.reports = []

    def report(self, progress, message):
        self.reports.append((progress, message))


class _Provider:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def candidates(self, run, as_of, asset_types, progress_callback=None):
        self.calls.append((run, as_of, asset_types))
        if progress_callback:
            progress_callback(50, "测试候选计算中")
        return self.rows


class _Service:
    def __init__(self, status="running"):
        self.run = {
            "run_id": 7,
            "status": status,
            "deleted_at": None,
            "strategy_code": "medium_term",
            "asset_types_json": json.dumps(["stock", "etf"]),
            "primary_signal_source": "legacy",
            "shadow_signal_source": "new",
            "plan_windows_json": json.dumps(["pre_market", "midday"]),
        }

    def _require_run(self, run_id):
        if run_id != 7:
            raise TradeRunError("TRADE_RUN_NOT_FOUND", "交易实例不存在", 404)
        return self.run


class _SubmittedTask:
    task_id = "scan-task-1"
    status = "running"

    def to_dict(self, include_result=False):
        return {"task_id": self.task_id, "name": "market_scan", "status": self.status,
                "params": {"run_id": 7}, "from_db": False}


class _TaskManager:
    def __init__(self):
        self.submitted = None
        self.memory = []
        self.history = []

    def submit(self, name, fn, *args, params=None, **kwargs):
        self.submitted = {"name": name, "fn": fn, "args": args, "params": params}
        return _SubmittedTask()

    def list_tasks(self, limit, name_filter):
        return self.memory

    def list_history(self, name, limit):
        return self.history

    def get_or_db(self, task_id):
        rows = [t.to_dict() for t in self.memory] + self.history
        return next((row for row in rows if row["task_id"] == task_id), None)


class MarketScanTests(unittest.TestCase):
    def test_scan_returns_one_execution_strategy_candidate_pool_without_writing_plans(self):
        legacy = _Provider([
            {
                "ts_code": "600000.SH", "asset_type": "stock", "side": "buy",
                "reference_price": 10.25, "score": 0.82,
                "reason": "旧体系多因子排名入选", "data_status": "delayed",
                "data_source": "legacy_screen", "market_time": datetime(2026, 8, 17, 8, 45),
            },
        ])
        unused = _Provider([])
        task = _ProgressRecorder()

        result = run_market_scan(
            task, _Service(), 7, "pre_market", datetime(2026, 8, 17, 8, 45),
            providers={"legacy": legacy, "new": unused},
        )

        self.assertEqual(result["run_id"], 7)
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["candidates"][0]["candidate_status"], "eligible")
        self.assertTrue(result["candidates"][0]["execution_confirmation_required"])
        self.assertNotIn("primary_candidates", result)
        self.assertNotIn("shadow_candidates", result)
        self.assertEqual(legacy.calls[0][2], {"stock", "etf"})
        self.assertEqual(unused.calls, [])
        self.assertEqual(task.reports[-1][0], 95)

    def test_scan_requires_a_running_trade_run_and_enabled_window(self):
        with self.assertRaisesRegex(TradeRunError, "只有运行中的交易实例"):
            run_market_scan(
                _ProgressRecorder(), _Service(status="paused"), 7, "pre_market",
                datetime(2026, 8, 17, 8, 45), providers={},
            )

        service = _Service()
        service.run["plan_windows_json"] = json.dumps(["midday"])
        with self.assertRaisesRegex(TradeRunError, "未启用"):
            run_market_scan(
                _ProgressRecorder(), service, 7, "pre_market",
                datetime(2026, 8, 17, 8, 45), providers={},
            )

    def test_submit_and_task_history_are_bound_to_one_trade_run(self):
        manager = _TaskManager()
        submitted = submit_market_scan(
            manager, _Service(), 7, "pre_market", datetime(2026, 8, 17, 8, 45),
        )
        self.assertEqual(submitted.task_id, "scan-task-1")
        self.assertEqual(manager.submitted["name"], "market_scan")
        self.assertEqual(manager.submitted["params"]["run_id"], 7)
        self.assertEqual(manager.submitted["params"]["as_of"], "2026-08-17T08:45:00")

        manager.memory = [_SubmittedTask()]
        manager.history = [
            {"task_id": "scan-task-old", "name": "market_scan", "status": "done",
             "params": {"run_id": 7}, "created_at": "2026-08-16T08:45:00", "from_db": True},
            {"task_id": "scan-task-other", "name": "market_scan", "status": "done",
             "params": {"run_id": 8}, "created_at": "2026-08-16T08:45:00", "from_db": True},
        ]
        rows = list_market_scan_tasks(manager, 7)
        self.assertEqual({row["task_id"] for row in rows}, {"scan-task-1", "scan-task-old"})
        self.assertEqual(get_market_scan_task(manager, 7, "scan-task-1")["task_id"], "scan-task-1")
        self.assertIsNone(get_market_scan_task(manager, 8, "scan-task-1"))

    def test_task_detail_keeps_a_progress_timeline_while_running(self):
        task = Task("market_scan", lambda: None, params={"run_id": 7})
        task.report(5, "校验交易实例与扫描窗口")
        task.report(42, "主策略：获取历史日线：300/1200，失败 0")
        detail = task.to_dict(include_result=False)

        self.assertEqual(detail["progress"], 42)
        self.assertEqual(
            [event["message"] for event in detail["progress_events"]],
            ["校验交易实例与扫描窗口", "主策略：获取历史日线：300/1200，失败 0"],
        )
        self.assertEqual(detail["progress_events"][-1]["progress"], 42)
