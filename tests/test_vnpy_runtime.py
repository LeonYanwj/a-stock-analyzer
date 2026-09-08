import unittest
from types import SimpleNamespace

from vnpy_runtime.config import VnpySettings
from vnpy_runtime.gateway import _normalize_vt_symbol, VnpyRuntime
from vnpy_runtime.strategy import equity_demo_strategy_info
from vnpy_runtime.history import sync_history


class VnpyRuntimeConfigTests(unittest.TestCase):
    def test_token_is_never_exposed_in_public_settings(self):
        settings = VnpySettings.from_config(SimpleNamespace(
            XTPY_MODE="token", XT_TOKEN="secret-token-value",
            XT_ACCOUNT_ID="", XT_ACCOUNT_TYPE="股票", XT_PATH="",
        ))
        self.assertTrue(settings.configured)
        self.assertNotIn("secret-token-value", str(settings.public_dict()))
        self.assertTrue(settings.public_dict()["token_configured"])

    def test_client_mode_requires_path(self):
        settings = VnpySettings.from_config(SimpleNamespace(
            XTPY_MODE="client", XT_TOKEN="", XT_ACCOUNT_ID="",
            XT_ACCOUNT_TYPE="股票", XT_PATH="",
        ))
        self.assertFalse(settings.configured)

    def test_strategy_summary_is_explicit(self):
        info = equity_demo_strategy_info()
        self.assertEqual(info["name"], "EquityDemoStrategy")
        self.assertTrue(any("排名" in item for item in info["logic"]))

    def test_old_project_exchange_suffix_is_normalized(self):
        self.assertEqual(_normalize_vt_symbol("000001.SZ"), "000001.SZSE")
        self.assertEqual(_normalize_vt_symbol("600000.SH"), "600000.SSE")

    def test_runtime_status_does_not_require_optional_packages(self):
        runtime = VnpyRuntime(VnpySettings(xt_token=None))
        status = runtime.status()
        self.assertFalse(status["settings"]["configured"])
        self.assertEqual(status["latest_ticks"], [])

    def test_history_sync_rejects_path_outside_project(self):
        runtime = VnpyRuntime(VnpySettings(xt_token="placeholder"))
        runtime.connected = True
        runtime.main_engine = object()
        with self.assertRaisesRegex(RuntimeError, "项目目录内"):
            sync_history(runtime, ["000001.SZ"], None, None, "/tmp/outside")

        with self.assertRaisesRegex(RuntimeError, "项目目录内"):
            sync_history(runtime, ["000001.SZ"], None, None, ".")
