"""APScheduler 集成：每个交易日傍晚自动更新行情 + 跑 daily_runner

随 FastAPI(uvicorn) 进程一起启动，进程在则调度在。

任务串行（同一个 job 内顺序执行，天然无竞态）：
  1. 更新所有活跃账户持仓股的当日 K 线 → market_daily
  2. 跑 daily_runner（止损 / 调仓 / 权益快照 / 复盘）

为什么用 BackgroundScheduler：daily_runner 是同步阻塞、可能跑几分钟的重活，
放后台线程跑，不会卡住 FastAPI 的事件循环。

时区固定 Asia/Shanghai，cron 看的是北京时间。
"""
import traceback
from datetime import date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import paper_engine as eng

# A 股收盘后、数据源就绪的时间。18:00 抓行情，留足数据源更新时间。
DAILY_HOUR = 18
DAILY_MINUTE = 0

scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

# 最近一次运行状态，供 GET /api/scheduler/status 查询
_last_run = {
    "status": "never",      # never / running / ok / error
    "started_at": None,
    "finished_at": None,
    "quotes_updated": 0,
    "detail": "",
}


def refresh_quotes_for_active_positions(trade_date=None) -> int:
    """更新所有活跃账户持仓股的日线到最新（写回 market_daily）

    只更新持仓股（止损要用它们的当日收盘价）；选股池由调仓日的
    screen_market 自己负责拉取。逐只调 get_daily：DB 已覆盖范围会直接
    命中 DB，缺当日数据时自动走 API 拉新并写回。

    Returns: 成功更新的股票只数
    """
    from data.fetcher import DataFetcher

    end = trade_date or date.today()
    end_s = end.strftime("%Y%m%d")
    start_s = (end - timedelta(days=20)).strftime("%Y%m%d")

    # 收集所有活跃账户的持仓股票代码（去重）
    codes = set()
    accts = eng.list_accounts()
    if not accts.empty:
        accts = accts[accts["is_active"] == 1]
        for aid in accts["account_id"]:
            pos = eng.get_positions(int(aid))
            if not pos.empty:
                codes.update(pos["ts_code"].tolist())

    if not codes:
        return 0

    fetcher = DataFetcher()
    n = 0
    for tc in codes:
        try:
            df = fetcher.get_daily(tc, start_s, end_s, adjust="qfq", source="sina")
            if df is not None and not df.empty:
                n += 1
        except Exception as e:
            print(f"  [warn] 行情更新失败 {tc}: {type(e).__name__}: {str(e)[:60]}")
    return n


def run_daily_job(trade_date=None):
    """调度主任务：先更新行情，再跑 daily_runner。失败不抛出（记录到状态）。"""
    import daily_runner

    _last_run.update(status="running", started_at=datetime.now().isoformat(),
                     finished_at=None, detail="更新持仓股行情中...")
    try:
        n = refresh_quotes_for_active_positions(trade_date)
        _last_run["quotes_updated"] = n
        _last_run["detail"] = f"行情更新 {n} 只，正在跑 daily_runner..."

        daily_runner.run_all(trade_date=trade_date)

        _last_run.update(
            status="ok", finished_at=datetime.now().isoformat(),
            detail=f"完成：更新 {n} 只持仓股行情 + 全部活跃账户 daily_runner")
    except Exception as e:
        _last_run.update(status="error", finished_at=datetime.now().isoformat(),
                         detail=f"{type(e).__name__}: {e}")
        traceback.print_exc()


def trigger_now():
    """手动立即触发一次（后台线程跑，立即返回）。供 API 验证用。"""
    scheduler.add_job(run_daily_job, id="manual_run", replace_existing=True,
                      max_instances=1, misfire_grace_time=None)


def get_status() -> dict:
    """返回最近一次运行状态 + 下次自动执行时间。"""
    job = scheduler.get_job("daily_job")
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else None
    return {
        "scheduler_running": scheduler.running,
        "next_run_time": next_run,
        "cron": f"mon-fri {DAILY_HOUR:02d}:{DAILY_MINUTE:02d} (Asia/Shanghai)",
        "last_run": dict(_last_run),
    }


def start_scheduler():
    """在 FastAPI 启动时调用：注册定时任务并启动调度器。"""
    if scheduler.running:
        return
    scheduler.add_job(
        run_daily_job,
        CronTrigger(day_of_week="mon-fri", hour=DAILY_HOUR, minute=DAILY_MINUTE),
        id="daily_job",
        name="每交易日傍晚 更新行情 + daily_runner",
        max_instances=1,        # 不允许重叠运行（上一轮没跑完就跳过本轮）
        coalesce=True,          # 错过多次只补跑一次
        misfire_grace_time=3600,  # 进程晚启动 1 小时内仍补跑
    )
    scheduler.start()


def shutdown_scheduler():
    """在 FastAPI 关闭时调用：优雅停掉调度器。"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
