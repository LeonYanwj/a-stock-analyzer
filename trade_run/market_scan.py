"""交易实例的市场扫描。

扫描是只读的研究步骤：给定一个运行中的交易实例、扫描窗口和数据截面，调用当前
执行策略并返回可解释的候选池。它不创建 ``signal_plan``，不改动现金、持仓或交易
实例状态；将候选转为计划仍由独立的确认动作负责。
"""
import json
from datetime import datetime
from typing import Any, Dict, Optional

from .models import RunStatus, TradeRunError, require
from .planner import WINDOWS, TradeRunPlanner
from .signal_providers import SignalProviderError


_BLOCKED_DATA_STATUSES = {"missing", "stale", "invalid"}


def submit_market_scan(task_manager, service, run_id: int, plan_window: str,
                       as_of: Optional[datetime] = None):
    """提交后台扫描任务，并返回底层通用任务对象。"""
    as_of = as_of or datetime.now()
    run = _validate_scan_request(service, run_id, plan_window)
    params = {
        "run_id": run_id,
        "task_type": "market_scan",
        "plan_window": plan_window,
        "as_of": as_of.isoformat(),
        "strategy_code": run["strategy_code"],
        "asset_types": json.loads(run["asset_types_json"]),
    }
    return task_manager.submit(
        "market_scan", run_market_scan, service, run_id, plan_window, as_of,
        params=params,
    )


def run_market_scan(task, service, run_id: int, plan_window: str,
                    as_of: datetime, providers: Optional[Dict[str, Any]] = None) -> dict:
    """执行一次只读扫描，并通过 ``task.report`` 汇报阶段进度。"""
    task.report(5, "校验交易实例与扫描窗口")
    run = _validate_scan_request(service, run_id, plan_window)
    asset_types = set(json.loads(run["asset_types_json"]))
    providers = providers or TradeRunPlanner(service).providers

    signal_source = run["primary_signal_source"]
    task.report(15, f"读取冻结策略与数据截面：{as_of.isoformat()}")
    rows = _scan_source(
        task, providers, signal_source, run, as_of, asset_types, 20, 90, "执行策略",
    )
    task.report(92, "整理候选池与数据状态")

    candidates = [_serialize_candidate(row, as_of) for row in rows]
    result = {
        "run_id": run_id,
        "plan_window": plan_window,
        "as_of": as_of.isoformat(),
        "strategy_code": run["strategy_code"],
        "asset_types": sorted(asset_types),
        "trading_mode": "manual_fill",
        "quote_reliability": "not_realtime",
        "message": "扫描结果仅为候选池；确认后才能生成交易计划，实际成交仍需人工确认券商报价并回填。",
        "candidates": candidates,
        "candidate_count": len(candidates),
    }
    task.report(95, "市场扫描完成，候选池已生成")
    return result


def list_market_scan_tasks(task_manager, run_id: int, limit: int = 30) -> list:
    """列出一个交易实例的扫描任务（当前进程任务 + 已归档历史）。"""
    rows = [t.to_dict(include_result=False)
            for t in task_manager.list_tasks(limit=limit * 2, name_filter="market_scan")]
    rows.extend(task_manager.list_history(name="market_scan", limit=limit * 2))
    unique = {}
    for row in rows:
        params = row.get("params") or {}
        if str(params.get("run_id")) != str(run_id):
            continue
        task_id = row.get("task_id")
        if task_id not in unique or not unique[task_id].get("from_db", False):
            unique[task_id] = row
    return sorted(unique.values(), key=lambda x: x.get("created_at") or "", reverse=True)[:limit]


def get_market_scan_task(task_manager, run_id: int, task_id: str) -> Optional[dict]:
    """读取单个扫描任务，并确保它属于请求中的交易实例。"""
    task = task_manager.get_or_db(task_id)
    if not task or task.get("name") != "market_scan":
        return None
    params = task.get("params") or {}
    if str(params.get("run_id")) != str(run_id):
        return None
    return task


def _validate_scan_request(service, run_id: int, plan_window: str) -> dict:
    require(plan_window in WINDOWS, "INVALID_PLAN_WINDOW", "市场扫描仅支持 pre_market 或 midday")
    run = service._require_run(run_id)
    require(run["deleted_at"] is None and run["status"] == RunStatus.RUNNING.value,
            "RUN_NOT_RUNNING", "只有运行中的交易实例可以扫描市场", 409)
    enabled_windows = set(json.loads(run["plan_windows_json"]))
    require(plan_window in enabled_windows, "PLAN_WINDOW_DISABLED", "该交易实例未启用此扫描窗口", 409)
    return run


def _scan_source(task, providers, source: str, run: dict, as_of: datetime,
                 asset_types: set, start_progress: int, end_progress: int, label: str) -> list:
    provider = providers.get(source)
    if provider is None:
        raise TradeRunError("SIGNAL_PROVIDER_NOT_CONFIGURED", f"{label}信号提供器未配置", 503, source)
    task.report(start_progress, f"{label}正在扫描 {', '.join(sorted(asset_types))} 候选范围")
    def report_source_progress(progress, message):
        mapped = start_progress + round((end_progress - start_progress) * max(0, min(100, progress)) / 100)
        task.report(mapped, f"{label}：{message}")
    try:
        rows = provider.candidates(run, as_of, asset_types,
                                   progress_callback=report_source_progress)
    except SignalProviderError as exc:
        raise TradeRunError("MARKET_SCAN_FAILED", f"{label}扫描失败", 503, str(exc)[:200])
    except Exception as exc:
        raise TradeRunError("MARKET_SCAN_FAILED", f"{label}扫描失败", 503,
                            f"{type(exc).__name__}: {exc}"[:200])
    task.report(end_progress, f"{label}扫描完成：{len(rows)} 个候选")
    return rows


def _serialize_candidate(row: dict, scan_as_of: datetime) -> dict:
    """把信号提供器内部行转换为前端可直接展示的候选契约。"""
    item = dict(row)
    data_status = item.get("data_status") or "unknown"
    blocked_reason = item.get("blocked_reason")
    if not blocked_reason and data_status in _BLOCKED_DATA_STATUSES:
        blocked_reason = f"数据状态为 {data_status}"
    market_time = item.get("market_time") or scan_as_of
    if hasattr(market_time, "isoformat"):
        market_time = market_time.isoformat()
    reference_price = item.get("reference_price")
    if reference_price is not None:
        reference_price = float(reference_price)
    score = item.get("score")
    if score is not None:
        score = float(score)
    return {
        "ts_code": item.get("ts_code"),
        "asset_type": item.get("asset_type"),
        "side": item.get("side", "buy"),
        "candidate_status": "blocked" if blocked_reason else "eligible",
        "blocked_reason": blocked_reason,
        "reference_price": reference_price,
        "suggested_price_range": _price_range(reference_price),
        "score": score,
        "reason": item.get("reason", "策略候选"),
        "data_status": data_status,
        "data_source": item.get("data_source", "unknown"),
        "data_as_of": market_time,
        "execution_confirmation_required": data_status == "delayed",
        "evidence": _json_safe({
            key: value for key, value in item.items()
            if key not in {"ts_code", "asset_type", "side", "reference_price", "score", "reason",
                           "data_status", "data_source", "market_time", "blocked_reason"}
        }),
    }


def _price_range(reference_price):
    if reference_price is None:
        return None
    return {
        "min_price": round(reference_price * 0.99, 4),
        "max_price": round(reference_price * 1.01, 4),
    }


def _json_safe(value):
    """保留候选证据，同时保证任务结果可写入 JSON 归档。"""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return {"raw": str(value)}
