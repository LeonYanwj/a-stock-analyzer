"""API 契约测试。

当前开发解释器没有 FastAPI/Pydantic 时跳过；领域账务测试仍始终执行。
CI 或部署环境安装 requirements.txt 后，本测试通过临时 SQLite 服务验证路由。
"""
import unittest

try:
    from fastapi.testclient import TestClient
    from api.routes.trade_runs import configure_service
    from api.main import app
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

from trade_run.repository import SqliteTradeRunRepository
from trade_run.service import TradeRunService


@unittest.skipUnless(FASTAPI_AVAILABLE, "当前解释器未安装 FastAPI/Pydantic")
class TradeRunApiTests(unittest.TestCase):
    def setUp(self):
        repo = SqliteTradeRunRepository()
        repo.initialize()
        configure_service(TradeRunService(repo))
        self.client = TestClient(app)

    def test_create_start_fill_and_dashboard_contract(self):
        created = self.client.post("/api/trade-runs", json={
            "name": "API 验证", "strategy_code": "short_term", "capital": 100000,
            "max_position_pct": 0.8, "asset_types": ["stock"],
        })
        self.assertEqual(created.status_code, 200)
        run_id = created.json()["run_id"]
        self.assertEqual(self.client.post(f"/api/trade-runs/{run_id}/start").status_code, 200)
        filled = self.client.post(f"/api/trade-runs/{run_id}/fills", json={
            "idempotency_key": "api-fill-1", "ts_code": "600000.SH", "asset_type": "stock",
            "side": "buy", "qty": 1000, "price": 10, "fee": 5,
            "executed_at": "2026-08-13T09:35:00", "source": "manual",
        })
        self.assertEqual(filled.status_code, 200)
        dash = self.client.get(f"/api/trade-runs/{run_id}/dashboard")
        self.assertEqual(dash.status_code, 200)
        self.assertEqual(dash.json()["cash"], 89995.0)

    def test_deleted_run_returns_structured_error(self):
        created = self.client.post("/api/trade-runs", json={
            "name": "删除验证", "strategy_code": "medium_term", "capital": 100000,
            "max_position_pct": 0.8, "asset_types": ["etf"],
        }).json()
        run_id = created["run_id"]
        self.assertEqual(self.client.delete(f"/api/trade-runs/{run_id}").status_code, 200)
        response = self.client.post(f"/api/trade-runs/{run_id}/start")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "TRADE_RUN_DELETED")
