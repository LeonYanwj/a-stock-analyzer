"""交易实例计划编排：主/影子信号、快照、仓位、比较和幂等。"""
from datetime import timedelta

from .models import PLAN_WINDOWS, RunStatus, TradeRunError, require
from .signal_providers import LegacySignalProvider, RuleSignalProvider, SignalProviderError


WINDOWS = {
    "pre_market": {"valid_from": (9, 20), "expires": (11, 30)},
    "midday": {"valid_from": (13, 0), "expires": (14, 50)},
}


class TradeRunPlanner:
    def __init__(self, service, providers=None):
        self.service = service
        self.repo = service.repo
        self.providers = providers or {
            "legacy": LegacySignalProvider(self.repo),
            "new": RuleSignalProvider(self.repo),
        }

    def generate(self, run_id, plan_window, as_of):
        require(plan_window in WINDOWS, "INVALID_PLAN_WINDOW", "自动计划窗口仅支持 pre_market 或 midday")
        run = self.service._require_run(run_id)
        require(run["status"] == RunStatus.RUNNING.value and run["deleted_at"] is None,
                "RUN_NOT_RUNNING", "只有运行中的交易实例可以生成计划", 409)
        enabled = set(__import__("json").loads(run["plan_windows_json"]))
        require(plan_window in enabled, "PLAN_WINDOW_DISABLED", "该实例未启用此计划窗口", 409)
        primary = run["primary_signal_source"]
        shadow = run["shadow_signal_source"]
        plan_date = as_of.date().isoformat()
        with self.repo.transaction():
            claimed = self.repo.claim_plan_generation(run_id, plan_window, plan_date)
        already = self.repo.list_plans_for_window(run_id, primary, plan_window, plan_date)
        if already:
            return {"run_id": run_id, "plan_window": plan_window, "idempotent": True,
                    "primary_plan_count": len(already), "shadow_plan_count": len(self.repo.list_plans_for_window(run_id, shadow, plan_window, plan_date))}
        if not claimed:
            generation = self.repo.get_plan_generation(run_id, plan_window, plan_date)
            if generation and generation["status"] == "generated":
                return {"run_id": run_id, "plan_window": plan_window, "idempotent": True,
                        "primary_plan_count": 0, "shadow_plan_count": 0}
        require(claimed, "PLAN_GENERATION_IN_PROGRESS", "同一实例该计划窗口正在生成或已处理", 409)
        try:
            primary_rows = self.providers[primary].candidates(run, as_of, set(__import__("json").loads(run["asset_types_json"])))
            shadow_rows = self.providers[shadow].candidates(run, as_of, set(__import__("json").loads(run["asset_types_json"])))
        except (SignalProviderError, Exception) as exc:
            with self.repo.transaction():
                self.repo.add_risk_event(run_id, "PLAN_GENERATION_FAILED", "high", "计划生成失败", {"window": plan_window, "error": f"{type(exc).__name__}: {exc}"})
                self.repo.add_audit(run_id, "PLAN_GENERATION_FAILED", "计划生成失败", {"window": plan_window})
                self.repo.finish_plan_generation(run_id, plan_window, plan_date, "failed", str(exc)[:500])
            raise TradeRunError("PLAN_GENERATION_FAILED", "计划生成失败，已写入风险事件", 503, str(exc)[:200])
        # 候选生成可能比用户的暂停操作耗时更久。落库前必须重新读取运行状态，
        # 不能让已暂停/结束的实例在后台任务返回后继续产生可执行计划。
        latest_run = self.service._require_run(run_id)
        if latest_run["status"] != RunStatus.RUNNING.value or latest_run["deleted_at"] is not None:
            with self.repo.transaction():
                self.repo.add_audit(run_id, "PLAN_GENERATION_CANCELLED", "交易实例已停止，丢弃未落库计划", {"window": plan_window})
                self.repo.finish_plan_generation(run_id, plan_window, plan_date, "cancelled", "交易实例已停止")
            raise TradeRunError("PLAN_GENERATION_CANCELLED", "交易实例已停止，未生成新计划", 409)
        primary_plans = self._persist(run, primary_rows, primary, plan_window, as_of)
        shadow_plans = self._persist(run, shadow_rows, shadow, plan_window, as_of)
        self._compare(run_id, plan_date, plan_window, primary_plans, shadow_plans)
        with self.repo.transaction():
            self.repo.finish_plan_generation(run_id, plan_window, plan_date, "generated")
        return {"run_id": run_id, "plan_window": plan_window, "idempotent": False,
                "primary_plan_count": len(primary_plans), "shadow_plan_count": len(shadow_plans)}

    def _persist(self, run, rows, source, plan_window, as_of):
        saved = []
        for item in rows:
            price = float(item["reference_price"])
            # 仓位计算仅基于冻结总仓位，数量向下取整为 100 股/份，避免超额承诺。
            budget = float(run["initial_capital"]) * float(run["max_position_pct"]) / max(1, len(rows))
            qty = int(budget / price / 100) * 100
            if qty < 100:
                continue
            observation_id = None
            with self.repo.transaction():
                observation_id = self.repo.insert_observation((run["run_id"], item["ts_code"], item.get("data_source", "unknown"), item.get("market_time", as_of).isoformat(sep=" ") if hasattr(item.get("market_time", as_of), "isoformat") else item.get("market_time"), self.repo.now(), None, "partial" if item.get("data_status") == "delayed" else "complete", None, __import__("json").dumps(item, ensure_ascii=False, default=str)))
            w = WINDOWS[plan_window]
            valid_from = as_of.replace(hour=w["valid_from"][0], minute=w["valid_from"][1], second=0, microsecond=0)
            expires = as_of.replace(hour=w["expires"][0], minute=w["expires"][1], second=0, microsecond=0)
            evidence = dict(item)
            evidence.update({
                "strategy_version_id": run["strategy_version_id"],
                "algorithm_fingerprint": __import__("json").loads(run["frozen_config_json"])["algorithm_fingerprint"],
                "position_budget": round(budget, 2),
                "research_price_source": item.get("data_source", "unknown"),
                "execution_reference": "券商报价进入计划价格区间后人工确认",
                "risk_check": {"total_position_limit": run["max_position_pct"], "qty_lot": 100},
            })
            saved.append(self.service.create_plan(run["run_id"], item["ts_code"], item["asset_type"], item["side"], qty, price,
                         round(price * .99, 4), round(price * 1.01, 4), item.get("data_status", "delayed"), None,
                         valid_from.isoformat(), expires.isoformat(), item["reason"], evidence,
                         source, plan_window, as_of.isoformat(), observation_id,
                         item.get("data_status", "delayed") == "delayed"))
        return saved

    def _compare(self, run_id, plan_date, plan_window, primary_plans, shadow_plans):
        primary = {(p["ts_code"], p["side"]): p for p in primary_plans}
        shadow = {(p["ts_code"], p["side"]): p for p in shadow_plans}
        with self.repo.transaction():
            for key in sorted(set(primary) | set(shadow)):
                p, s = primary.get(key), shadow.get(key)
                kind = "overlap" if p and s else ("primary_only" if p else "shadow_only")
                self.repo.insert_comparison((run_id, plan_date, plan_window, key[0], key[1], kind,
                                             p["plan_id"] if p else None, s["plan_id"] if s else None,
                                             None, None if kind == "overlap" else "仅记录机会差异，不计算伪造收益", self.repo.now()))
