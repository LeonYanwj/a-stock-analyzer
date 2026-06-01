"""调度器查询 / 手动触发接口"""
from fastapi import APIRouter

from api import scheduler as sched

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


@router.get("/status")
def scheduler_status():
    """调度器状态：是否运行、下次执行时间、最近一次运行结果

    用它确认定时任务真的在跑：
      - scheduler_running=true 且 next_run_time 有值 → 调度已就绪
      - last_run.status: never(从没跑过) / running / ok / error
    """
    return sched.get_status()


@router.post("/run-now")
def scheduler_run_now():
    """手动立即触发一次（更新行情 + daily_runner），后台跑，立即返回。

    用于补数据或验证调度逻辑，不必等到傍晚。跑完用 GET /status 看结果。
    """
    sched.trigger_now()
    return {"ok": True, "message": "已触发，后台运行中。用 GET /api/scheduler/status 查看进度"}
