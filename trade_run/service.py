"""交易实例领域服务：状态、计划和成交事实的唯一写入口。"""
import json
from datetime import datetime

from .models import (ASSET_TYPES, PLAN_WINDOWS, RUNNABLE_STATUSES, SIGNAL_SOURCES,
                     STRATEGY_CODES, PlanStatus, RunStatus, Side, TradeRunError, require)


class TradeRunService:
    def __init__(self, repo):
        self.repo = repo

    def create_run(self, name, strategy_code, capital, max_position_pct, asset_types,
                   signal_source=None, shadow_signal_source=None,
                   plan_windows=None):
        require(bool(name and name.strip()), "INVALID_RUN_NAME", "交易实例名称不能为空")
        require(strategy_code in STRATEGY_CODES, "UNKNOWN_STRATEGY", "不支持的策略代码")
        require(float(capital) > 0, "INVALID_CAPITAL", "初始资金必须大于 0")
        require(0 < float(max_position_pct) <= 1, "INVALID_MAX_POSITION", "总仓位必须在 0 到 1 之间")
        asset_types = sorted(set(asset_types or []))
        require(bool(asset_types) and set(asset_types) <= ASSET_TYPES, "INVALID_ASSET_TYPES", "资产范围仅支持 stock 和 etf")
        require(signal_source in SIGNAL_SOURCES, "SIGNAL_SOURCE_REQUIRED", "必须明确选择主信号体系 legacy 或 new")
        shadow_signal_source = shadow_signal_source or ("new" if signal_source == "legacy" else "legacy")
        require(shadow_signal_source in SIGNAL_SOURCES and shadow_signal_source != signal_source,
                "INVALID_SHADOW_SIGNAL_SOURCE", "影子体系必须是与主体系不同的 legacy 或 new")
        plan_windows = plan_windows or ["pre_market", "midday"]
        require(bool(plan_windows) and set(plan_windows) <= PLAN_WINDOWS - {"manual"},
                "INVALID_PLAN_WINDOWS", "计划窗口仅支持 pre_market 和 midday")
        version = self.repo.latest_strategy_version(strategy_code, signal_source)
        require(version is not None, "STRATEGY_VERSION_NOT_FOUND", "策略版本不存在", 404)
        now = self.repo.now()
        frozen = {"strategy_version": version["version_no"], "algorithm_fingerprint": version["algorithm_fingerprint"], "asset_types": asset_types, "max_position_pct": float(max_position_pct), "primary_signal_source": signal_source, "shadow_signal_source": shadow_signal_source, "plan_windows": plan_windows}
        with self.repo.transaction():
            run_id = self.repo.insert_run((name.strip(), strategy_code, version["version_id"], RunStatus.DRAFT.value, float(capital), float(capital), float(max_position_pct), json.dumps(asset_types), json.dumps(frozen, ensure_ascii=False), signal_source, shadow_signal_source, json.dumps(plan_windows), now))
            self.repo.add_cash_ledger(run_id, None, "initial_capital", float(capital), float(capital))
            self.repo.add_audit(run_id, "RUN_CREATED", "已创建交易实例", {"strategy_code": strategy_code, "strategy_version": version["version_no"], "primary_signal_source": signal_source, "shadow_signal_source": shadow_signal_source})
        return self.get_run(run_id)

    def get_run(self, run_id):
        run = self.repo.get_run(run_id)
        require(run is not None, "TRADE_RUN_NOT_FOUND", "交易实例不存在", 404)
        return self._serialize_run(run)

    def list_runs(self, include_deleted=False):
        return [self._serialize_run(r) for r in self.repo.list_runs(include_deleted)]

    def start_run(self, run_id):
        with self.repo.transaction():
            run = self._require_run(run_id, for_update=True)
            require(run["deleted_at"] is None, "TRADE_RUN_DELETED", "已删除的交易实例不能启动", 409)
            require(run["status"] in RUNNABLE_STATUSES, "INVALID_RUN_TRANSITION", "当前状态不能启动", 409, run["status"])
            now = self.repo.now()
            try:
                self.repo.update_run(run_id, status=RunStatus.RUNNING.value, active_strategy_code=run["strategy_code"], started_at=run["started_at"] or now, paused_at=None)
            except Exception as exc:
                if "UNIQUE" in str(exc).upper() or "DUPLICATE" in str(exc).upper():
                    raise TradeRunError("STRATEGY_RUN_ALREADY_ACTIVE", "该策略已有运行中的交易实例", 409, run["strategy_code"])
                raise
            self.repo.add_audit(run_id, "RUN_STARTED", "用户已启动交易实例", {"previous_status": run["status"]})
        return self.get_run(run_id)

    def stop_run(self, run_id, action="pause", reason="用户或系统停止"):
        require(action in {"pause", "end"}, "INVALID_STOP_ACTION", "停止动作仅支持 pause 或 end")
        with self.repo.transaction():
            run = self._require_run(run_id, for_update=True)
            require(run["status"] == RunStatus.RUNNING.value, "INVALID_RUN_TRANSITION", "只有运行中的实例可以停止", 409)
            now = self.repo.now()
            if action == "pause":
                self.repo.update_run(run_id, status=RunStatus.PAUSED.value, active_strategy_code=None, paused_at=now)
                self.repo.add_audit(run_id, "RUN_PAUSED", reason, {})
            else:
                self.repo.update_run(run_id, status=RunStatus.ENDED.value, active_strategy_code=None, ended_at=now)
                self.repo.add_audit(run_id, "RUN_ENDED", reason, {})
        return self.get_run(run_id)

    def delete_run(self, run_id):
        with self.repo.transaction():
            run = self._require_run(run_id, for_update=True)
            require(run["deleted_at"] is None, "TRADE_RUN_DELETED", "交易实例已删除", 409)
            self.repo.update_run(run_id, status=RunStatus.DELETED.value, active_strategy_code=None, deleted_at=self.repo.now())
            self.repo.add_audit(run_id, "RUN_DELETED", "用户逻辑删除交易实例，历史保留", {})
        return self.get_run(run_id)

    def create_plan(self, run_id, ts_code, asset_type, side, suggested_qty, reference_price,
                    min_price=None, max_price=None, data_status="delayed",
                    blocked_reason=None, valid_from=None, expires_at=None,
                    reason="待策略服务写入理由", evidence=None,
                    signal_source=None, plan_window="manual", as_of=None,
                    observation_id=None, execution_confirmation_required=None):
        with self.repo.transaction():
            run = self._require_run(run_id, for_update=True)
            require(run["status"] == RunStatus.RUNNING.value, "RUN_NOT_RUNNING", "只有运行中的交易实例可以生成计划", 409)
            allowed = set(json.loads(run["asset_types_json"]))
            require(asset_type in allowed, "ASSET_TYPE_NOT_ALLOWED", "该交易实例不允许此资产类型")
            self._validate_order(side, suggested_qty, reference_price)
            signal_source = signal_source or run.get("primary_signal_source", "legacy")
            require(signal_source in SIGNAL_SOURCES, "INVALID_SIGNAL_SOURCE", "信号体系仅支持 legacy 或 new")
            require(plan_window in PLAN_WINDOWS, "INVALID_PLAN_WINDOW", "计划窗口不合法")
            invalid_data = data_status in {"missing", "stale", "invalid"}
            status = PlanStatus.BLOCKED.value if blocked_reason or invalid_data else PlanStatus.ELIGIBLE.value
            confirmation_required = (data_status == "delayed") if execution_confirmation_required is None else bool(execution_confirmation_required)
            plan_id = self.repo.insert_plan((run_id, ts_code.upper(), asset_type, side, int(suggested_qty), float(reference_price), min_price, max_price, status, data_status, blocked_reason, valid_from, expires_at, 0, reason, json.dumps(evidence or {}, ensure_ascii=False, default=str), signal_source, plan_window, as_of, observation_id, int(confirmation_required), self.repo.now()))
            self.repo.add_audit(run_id, "PLAN_CREATED", "已生成手工执行计划", {"plan_id": plan_id, "status": status, "data_status": data_status, "signal_source": signal_source, "execution_confirmation_required": confirmation_required})
        return self.get_plan(run_id, plan_id)

    def get_plan(self, run_id, plan_id):
        plan = self.repo.get_plan(plan_id)
        require(plan and plan["run_id"] == run_id, "PLAN_NOT_FOUND", "交易计划不存在", 404)
        return self._serialize_plan(plan)

    def list_plans(self, run_id):
        self._require_run(run_id)
        return [self._serialize_plan(p) for p in self.repo.list_plans(run_id)]

    def record_fill(self, run_id, *, idempotency_key, ts_code, side, qty, price, fee, executed_at,
                    plan_id=None, asset_type=None, source="manual", note=None,
                    broker_quote_confirmed=False, quote_checked_at=None):
        require(bool(idempotency_key), "IDEMPOTENCY_KEY_REQUIRED", "成交回填必须提供幂等键")
        with self.repo.transaction():
            existing = self.repo.get_fill_by_key(idempotency_key)
            if existing:
                require(existing["run_id"] == run_id, "IDEMPOTENCY_KEY_CONFLICT", "幂等键已属于其他交易实例", 409)
                return {"fill": existing, "idempotent": True}
            run = self._require_run(run_id, for_update=True)
            require(run["status"] == RunStatus.RUNNING.value, "RUN_NOT_RUNNING", "只有运行中的交易实例可回填成交", 409)
            self._validate_order(side, qty, price)
            fee = float(fee or 0)
            require(fee >= 0, "INVALID_FEE", "费用不能为负数")
            require(bool(broker_quote_confirmed), "BROKER_QUOTE_CONFIRMATION_REQUIRED", "成交回填必须确认券商报价", 409)
            require(bool(quote_checked_at), "QUOTE_CHECKED_AT_REQUIRED", "成交回填必须记录券商报价确认时间")
            trade_date = self._trade_date(executed_at)
            self.repo.rollover_sellable(run_id, trade_date)
            plan = None
            if plan_id is not None:
                plan = self.repo.get_plan(plan_id)
                require(plan and plan["run_id"] == run_id, "PLAN_NOT_FOUND", "成交关联的交易计划不存在", 404)
                require(plan.get("signal_source", run.get("primary_signal_source")) == run.get("primary_signal_source"),
                        "SHADOW_PLAN_NOT_EXECUTABLE", "影子计划仅用于比较，不能回填真实成交", 409)
                require(plan["ts_code"] == ts_code.upper() and plan["side"] == side, "FILL_PLAN_MISMATCH", "成交与计划的证券或方向不一致")
                require(plan["status"] in {PlanStatus.ELIGIBLE.value, PlanStatus.PARTIALLY_FILLED.value}, "PLAN_NOT_EXECUTABLE", "该计划当前不可执行", 409, plan["status"])
                require(not plan["blocked_reason"] and plan["data_status"] not in {"missing", "stale", "invalid"}, "PLAN_BLOCKED", "该计划因行情或风控数据不可靠而被阻塞", 409, plan["data_status"])
                if plan.get("execution_confirmation_required"):
                    require(bool(broker_quote_confirmed), "BROKER_QUOTE_CONFIRMATION_REQUIRED", "延迟行情计划必须确认券商报价后才能回填成交", 409)
                if plan["min_price"] is not None:
                    require(float(price) + 1e-8 >= float(plan["min_price"]), "FILL_PRICE_OUT_OF_RANGE", "实际成交价低于计划价格区间", 409)
                if plan["max_price"] is not None:
                    require(float(price) <= float(plan["max_price"]) + 1e-8, "FILL_PRICE_OUT_OF_RANGE", "实际成交价高于计划价格区间", 409)
                filled_qty = self.repo.plan_fill_qty(plan_id)
                require(filled_qty + int(qty) <= int(plan["suggested_qty"]), "PLAN_QTY_EXCEEDED", "成交数量超过计划剩余数量", 409)
            amount = round(float(qty) * float(price), 2)
            cash = float(run["current_cash"])
            position = self.repo.get_position(run_id, ts_code.upper())
            if side == Side.BUY.value:
                require(cash + 1e-8 >= amount + fee, "INSUFFICIENT_CASH", "可用现金不足")
                current_cost = sum(float(p["qty"]) * float(p["avg_cost"]) for p in self.repo.list_positions(run_id))
                require(current_cost + amount + fee <= float(run["initial_capital"]) * float(run["max_position_pct"]) + 1e-8,
                        "MAX_POSITION_EXCEEDED", "成交后将超过该实例冻结的总仓位上限")
                new_cash = round(cash - amount - fee, 2)
                if position:
                    new_qty = position["qty"] + int(qty)
                    new_cost = round((position["qty"] * position["avg_cost"] + amount + fee) / new_qty, 6)
                    sellable = position["sellable_qty"]
                    open_date = position["open_date"] if position["qty"] > 0 else trade_date
                    realized = position["realized_pnl"]
                else:
                    new_qty, new_cost, sellable, open_date, realized = int(qty), round((amount + fee) / int(qty), 6), 0, trade_date, 0
                if plan:
                    asset_type = plan["asset_type"]
                elif asset_type is None:
                    allowed_types = set(json.loads(run["asset_types_json"]))
                    asset_type = next(iter(allowed_types)) if len(allowed_types) == 1 else None
                require(asset_type in set(json.loads(run["asset_types_json"])), "ASSET_TYPE_NOT_ALLOWED", "该交易实例不允许此资产类型")
                self.repo.upsert_position((run_id, ts_code.upper(), asset_type, new_qty, sellable, new_cost, realized, open_date, self.repo.now()))
                cash_entry = -(amount + fee)
            else:
                require(position is not None and position["qty"] >= int(qty), "INSUFFICIENT_POSITION", "可卖持仓数量不足")
                require(position["sellable_qty"] >= int(qty), "T1_SELL_RESTRICTED", "当日买入证券不可卖出")
                new_qty = position["qty"] - int(qty)
                new_cash = round(cash + amount - fee, 2)
                realized = round(position["realized_pnl"] + (float(price) - position["avg_cost"]) * int(qty) - fee, 2)
                self.repo.upsert_position((run_id, ts_code.upper(), position["asset_type"], new_qty, position["sellable_qty"] - int(qty), position["avg_cost"], realized, position["open_date"], self.repo.now()))
                cash_entry = amount - fee
            require(source == "manual", "UNSUPPORTED_FILL_SOURCE", "当前只支持 manual 成交回填", 400, source)
            fill_id = self.repo.insert_fill((run_id, plan_id, idempotency_key, ts_code.upper(), side, int(qty), float(price), fee, self._normalize_datetime(executed_at), source, note, int(bool(broker_quote_confirmed)), self._normalize_datetime(quote_checked_at) if quote_checked_at else None, self.repo.now()))
            if plan_id is not None:
                # 影子不建账；只记录与主计划同证券同方向的真实成交镜像关联。
                self.repo.mark_comparison_fill(plan_id, fill_id)
            self.repo.update_run(run_id, current_cash=new_cash)
            self.repo.add_cash_ledger(run_id, fill_id, "buy_fill" if side == Side.BUY.value else "sell_fill", cash_entry, new_cash)
            if plan:
                filled_qty = self.repo.plan_fill_qty(plan_id)
                next_status = PlanStatus.TRIGGERED.value if filled_qty >= int(plan["suggested_qty"]) else PlanStatus.PARTIALLY_FILLED.value
                self.repo.update_plan(plan_id, status=next_status, filled_qty=filled_qty)
            self.repo.add_audit(run_id, "FILL_RECORDED", "已回填实际成交并更新派生账务", {"fill_id": fill_id, "side": side, "qty": qty, "price": price, "broker_quote_confirmed": bool(broker_quote_confirmed), "quote_checked_at": quote_checked_at})
            return {"fill": self.repo.get_fill_by_key(idempotency_key), "idempotent": False}

    def positions(self, run_id):
        self._require_run(run_id)
        return self.repo.list_positions(run_id)

    def dashboard(self, run_id=None):
        if run_id is None:
            runs = self.list_runs()
            return {"runs": runs, "summary": {"total_runs": len(runs), "running_runs": sum(r["status"] == "running" for r in runs)}}
        run = self.get_run(run_id)
        positions = self.positions(run_id)
        market_value = round(sum(p["qty"] * p["avg_cost"] for p in positions), 2)
        equity = round(float(run["current_cash"]) + market_value, 2)
        return {"run": run, "cash": float(run["current_cash"]), "market_value": market_value, "market_value_source": "cost", "total_equity": equity, "return_pct": round(equity / float(run["initial_capital"]) - 1, 6), "positions": positions, "plan_counts": self.repo.plan_counts(run_id), "fill_count": len(self.repo.list_fills(run_id)), "recent_events": [self._serialize_event(e) for e in self.repo.list_events(run_id, 10)]}

    def performance(self, run_id):
        dash = self.dashboard(run_id)
        realized = round(sum(float(p["realized_pnl"]) for p in self.repo.list_all_positions(run_id)), 2)
        comparisons = self.repo.list_comparisons(run_id)
        return {"run_id": run_id, "initial_capital": dash["run"]["initial_capital"], "total_equity": dash["total_equity"], "return_pct": dash["return_pct"], "realized_pnl": realized, "valuation_status": "cost_based", "real_execution": {"realized_pnl": realized, "return_pct": dash["return_pct"]}, "overlap_shadow": {"comparison_count": sum(c["comparison_type"] == "overlap" for c in comparisons), "status": "仅在主影子同证券同方向且主计划真实成交时镜像成交"}, "opportunity_difference": {"count": sum(c["comparison_type"] != "overlap" for c in comparisons), "status": "非重合信号仅记录机会差异，不计算伪造收益"}, "warning": "当前未接入可信实时行情和基准快照；市值与未实现收益仅按持仓成本展示。"}

    def comparisons(self, run_id):
        self._require_run(run_id)
        rows = self.repo.list_comparisons(run_id)
        return {
            "overlap": [r for r in rows if r["comparison_type"] == "overlap"],
            "primary_only": [r for r in rows if r["comparison_type"] == "primary_only"],
            "shadow_only": [r for r in rows if r["comparison_type"] == "shadow_only"],
        }

    def events(self, run_id, limit=50):
        self._require_run(run_id)
        return [self._serialize_event(e) for e in self.repo.list_events(run_id, limit)]

    def _require_run(self, run_id, for_update=False):
        run = self.repo.get_run(run_id, for_update=for_update)
        require(run is not None, "TRADE_RUN_NOT_FOUND", "交易实例不存在", 404)
        return run

    @staticmethod
    def _validate_order(side, qty, price):
        require(side in {Side.BUY.value, Side.SELL.value}, "INVALID_SIDE", "方向仅支持 buy 或 sell")
        require(isinstance(qty, int) and qty > 0 and qty % 100 == 0, "INVALID_QTY", "数量必须为正整数且为 100 的整倍数")
        require(float(price) > 0, "INVALID_PRICE", "价格必须大于 0")

    @staticmethod
    def _trade_date(executed_at):
        try:
            return datetime.fromisoformat(executed_at.replace("Z", "+00:00")).date().isoformat()
        except (ValueError, AttributeError):
            raise TradeRunError("INVALID_EXECUTED_AT", "成交时间必须是 ISO 8601 格式")

    @staticmethod
    def _normalize_datetime(value):
        """统一落库为 MySQL DATETIME 可接受的无时区文本；交易日取自原始时间。"""
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            raise TradeRunError("INVALID_EXECUTED_AT", "成交时间必须是 ISO 8601 格式")
        return parsed.replace(tzinfo=None, microsecond=0).isoformat(sep=" ")

    @staticmethod
    def _serialize_run(run):
        out = dict(run)
        out["asset_types"] = json.loads(out.pop("asset_types_json"))
        out["frozen_config"] = json.loads(out.pop("frozen_config_json"))
        if "plan_windows_json" in out:
            out["plan_windows"] = json.loads(out.pop("plan_windows_json"))
        return out

    @staticmethod
    def _serialize_plan(plan):
        out = dict(plan)
        out["evidence"] = json.loads(out.pop("evidence_json"))
        if "execution_confirmation_required" in out:
            out["execution_confirmation_required"] = bool(out["execution_confirmation_required"])
        return out

    @staticmethod
    def _serialize_event(event):
        out = dict(event)
        out["payload"] = json.loads(out.pop("payload_json"))
        return out
