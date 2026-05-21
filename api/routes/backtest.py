"""回测查询 API + 异步触发"""
import os
import sys
import subprocess
import time
from typing import List, Optional
from fastapi import APIRouter

from data.db import get_conn, list_backtest_runs, get_backtest_detail
from api.schemas import BacktestRun
from api.errors import NotFound, BadRequest
from api import tasks as task_mgr
from strategies import list_strategies


router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.get("", response_model=List[BacktestRun])
def list_runs(strategy: Optional[str] = None, limit: int = 50):
    """历史回测列表"""
    with get_conn() as conn:
        df = list_backtest_runs(conn, strategy_name=strategy, limit=limit)
    if df.empty:
        return []
    return df.to_dict("records")


@router.get("/{run_id}")
def get_run(run_id: int):
    """回测详情（含 equity / positions / ic）"""
    with get_conn() as conn:
        detail = get_backtest_detail(conn, run_id)
    if detail is None:
        raise NotFound(f"回测 run_id={run_id} 不存在", code="BACKTEST_NOT_FOUND")
    result = {
        "run": {k: (str(v) if hasattr(v, "isoformat") else float(v) if hasattr(v, "real") and not isinstance(v, (int, bool)) else v)
                for k, v in detail["run"].items()},
    }
    if not detail["equity"].empty:
        result["equity"] = detail["equity"].to_dict("records")
    if not detail["positions"].empty:
        result["positions"] = detail["positions"].head(100).to_dict("records")
    if not detail["ic"].empty:
        # IC 按因子聚合
        ic = detail["ic"]
        ic_summary = ic.groupby("factor_name")["ic"].agg(["mean", "std", "count"]).reset_index()
        ic_summary["ir"] = ic_summary["mean"] / ic_summary["std"]
        result["ic_summary"] = ic_summary.to_dict("records")
    return result


# -------------------- 异步触发 --------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _do_run_backtest(task, strategy: str, months: int, limit: int,
                     top: int, capital: float, rebal_weeks: int):
    """异步任务：起子进程跑 backtest_simple.py，实时捕获输出

    跑完后从 DB 查最新 run_id 返回（如果入库成功）
    """
    script = os.path.join(PROJECT_ROOT, "backtest_simple.py")
    if not os.path.exists(script):
        raise NotFound(f"找不到回测脚本：{script}", code="BACKTEST_SCRIPT_MISSING")

    cmd = [sys.executable, script,
           "--strategy", strategy,
           "--months", str(months),
           "--limit", str(limit),
           "--rebal-weeks", str(rebal_weeks)]
    if top > 0:
        cmd += ["--top", str(top)]
    if capital > 0:
        cmd += ["--capital", str(capital)]

    task.report(5, f"启动回测子进程：{' '.join(cmd[1:])}")
    t0 = time.time()

    # 启动子进程，按行读取输出（用于估计进度）
    proc = subprocess.Popen(
        cmd, cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        bufsize=1,
    )

    log_lines = []
    # 关键阶段标记 → 进度
    phase_progress = {
        "[1/4]": 15, "[2/4]": 30, "[3/4]": 50, "[4/4]": 70,
        "回测完成": 95, "DB 入库": 98,
    }
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        log_lines.append(line)
        for keyword, pct in phase_progress.items():
            if keyword in line:
                task.report(pct, line[:60])
                break

    rc = proc.wait()
    elapsed = time.time() - t0
    full_log = "\n".join(log_lines)

    if rc != 0:
        raise RuntimeError(f"回测子进程退出码 {rc}，最后输出：\n{full_log[-500:]}")

    # 跑完查最新的 run_id
    task.report(99, "查询新建的 run_id...")
    run_id = None
    try:
        with get_conn() as conn:
            df = list_backtest_runs(conn, strategy_name=strategy, limit=1)
            if not df.empty:
                run_id = int(df.iloc[0]["run_id"])
    except Exception:
        pass

    return {
        "strategy": strategy,
        "months": months,
        "limit": limit,
        "elapsed_seconds": round(elapsed, 1),
        "run_id": run_id,
        "log_tail": "\n".join(log_lines[-30:]),
    }


@router.post("/run/async")
def run_backtest_async(
    strategy: str = "swing",
    months: int = 12,
    limit: int = 300,
    top: int = 0,
    capital: float = 0,
    rebal_weeks: int = 1,
):
    """【异步】触发一次回测（起子进程跑 backtest_simple.py）

    参数：
      - strategy:     short_term / swing / trend / ic_optimized
      - months:       回测月数（默认 12 = 1 年）
      - limit:        股票池规模（默认 300）
      - top:          每周选股数（0=按 capital 自动算）
      - capital:      模拟资金量（元）；传了会用精确成本模型
      - rebal_weeks:  调仓间隔周数（1=每周, 2=两周, 4=每月）

    跑完会自动入库，result.run_id 是新建的回测 ID，可用
    GET /api/backtest/{run_id} 查详情。
    """
    if strategy not in list_strategies():
        raise BadRequest(f"未知策略：{strategy}", code="UNKNOWN_STRATEGY",
                         detail=f"可选：{list_strategies()}")
    if months < 1 or months > 60:
        raise BadRequest("months 取值 1-60", code="INVALID_MONTHS")
    if limit < 10:
        raise BadRequest("limit 不小于 10", code="INVALID_LIMIT")

    params = {"strategy": strategy, "months": months, "limit": limit,
              "top": top, "capital": capital, "rebal_weeks": rebal_weeks}
    task = task_mgr.submit(
        "backtest", _do_run_backtest, params=params,
        strategy=strategy, months=months, limit=limit,
        top=top, capital=capital, rebal_weeks=rebal_weeks,
    )
    return {"task_id": task.task_id, "status": task.status,
            "tip": f"轮询 GET /api/tasks/{task.task_id}（回测耗时数分钟）"}
