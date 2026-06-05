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
from datetime import date, datetime, time, timedelta

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

# 盘中监控最近一次状态
_last_intraday = {
    "status": "never",   # never / ok
    "time": None,
    "sold": 0,
    "detail": [],
}

# 盘后持仓分析报告最近一次状态
_last_holding = {
    "status": "never",   # never / ok / skip / error
    "time": None,
    "count": 0,
    "mail": None,
    "reason": None,
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


def is_trade_day(d) -> bool:
    """d 是否 A 股交易日。查不到时保守返回 False（不确定时不操作）。"""
    try:
        from data.fetcher import DataFetcher
        return len(DataFetcher().get_trade_dates(d, d)) > 0
    except Exception:
        return False


def is_trading_hours(now) -> bool:
    """now 是否在 A 股交易时段（9:30-11:30 或 13:00-15:00）"""
    t = now.time()
    return (time(9, 30) <= t <= time(11, 30)) or (time(13, 0) <= t <= time(15, 0))


def run_intraday_monitor():
    """盘中 job：交易日 & 交易时段内，对需盘中监控的策略账户(短线)跑 intraday_monitor。

    cron 触发得宽(9-15 每10分钟)，这里精确校验时段；非交易日/非时段直接跳过。
    只评估持仓、只卖不买（买入放收盘）。
    """
    from strategies import get_intraday_minutes
    now = datetime.now()
    if not is_trade_day(now.date()) or not is_trading_hours(now):
        return
    try:
        accts = eng.list_accounts(status="active")
    except Exception:
        traceback.print_exc()
        return
    if accts.empty:
        return

    total_sold, detail = 0, []
    for _, a in accts.iterrows():
        if get_intraday_minutes(a["strategy_name"]) is None:
            continue   # 该策略不做盘中监控（如波段/趋势）
        try:
            r = eng.intraday_monitor(int(a["account_id"]), now.date())
            total_sold += r["sold"]
            if r["sold"]:
                detail.append({"account_id": int(a["account_id"]),
                               "account": a["account_name"], "items": r["detail"]})
        except Exception:
            traceback.print_exc()

    _last_intraday.update(status="ok", time=now.isoformat(),
                          sold=total_sold, detail=detail)


def run_holding_report():
    """盘后任务：对所有实盘持仓做全方位分析 + 邮件推送（每交易日 18:30）。"""
    try:
        import holding_analyzer
        r = holding_analyzer.run_and_notify()
        _last_holding.update(
            status="ok" if r.get("ok") else "skip",
            time=datetime.now().isoformat(),
            count=r.get("count", 0),
            mail=r.get("mail"),
            reason=r.get("reason"))
    except Exception as e:
        _last_holding.update(status="error", time=datetime.now().isoformat(),
                             reason=f"{type(e).__name__}: {e}")
        traceback.print_exc()


def trigger_now():
    """手动立即触发一次（后台线程跑，立即返回）。供 API 验证用。"""
    scheduler.add_job(run_daily_job, id="manual_run", replace_existing=True,
                      max_instances=1, misfire_grace_time=None)


def get_status() -> dict:
    """返回最近一次运行状态 + 下次自动执行时间。"""
    job = scheduler.get_job("daily_job")
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else None
    intraday = scheduler.get_job("intraday_job")
    intraday_next = (intraday.next_run_time.isoformat()
                     if intraday and intraday.next_run_time else None)
    holding = scheduler.get_job("holding_job")
    holding_next = (holding.next_run_time.isoformat()
                    if holding and holding.next_run_time else None)
    return {
        "scheduler_running": scheduler.running,
        "next_run_time": next_run,
        "cron": f"mon-fri {DAILY_HOUR:02d}:{DAILY_MINUTE:02d} (Asia/Shanghai)",
        "last_run": dict(_last_run),
        "intraday": {
            "next_run_time": intraday_next,
            "cron": "mon-fri 9:30-11:30 & 13:00-15:00 每10分钟 (Asia/Shanghai)",
            "last_run": dict(_last_intraday),
        },
        "holding_report": {
            "next_run_time": holding_next,
            "cron": "mon-fri 18:30 (Asia/Shanghai)",
            "last_run": dict(_last_holding),
        },
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
    # 盘中监控：交易日 9-15 点每 10 分钟触发，job 内部精确校验交易时段
    scheduler.add_job(
        run_intraday_monitor,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/10"),
        id="intraday_job",
        name="盘中每10分钟监控短线持仓(止损/MA5)",
        max_instances=1, coalesce=True,
        misfire_grace_time=120,   # 盘中时效强，过期 2 分钟就不补
    )
    # 盘后持仓分析：每交易日 18:30（排在 daily_runner 18:00 之后，行情已更新）
    scheduler.add_job(
        run_holding_report,
        CronTrigger(day_of_week="mon-fri", hour=18, minute=30),
        id="holding_job",
        name="盘后 实盘持仓全方位分析+邮件",
        max_instances=1, coalesce=True, misfire_grace_time=3600,
    )
    scheduler.start()


def shutdown_scheduler():
    """在 FastAPI 关闭时调用：优雅停掉调度器。"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
