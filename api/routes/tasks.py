"""任务管理 API（查询任务状态/进度/结果，含 DB 归档）"""
from typing import Optional
from fastapi import APIRouter
from api.errors import NotFound

from api import tasks as task_mgr


router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
def list_tasks(limit: int = 30, name: Optional[str] = None,
               status: Optional[str] = None):
    """列出内存中的任务（最近 N 个），可按 name 或 status 过滤

    只看进行中和最近的；历史归档任务用 /api/tasks/history 查 DB。

    Args:
        limit: 返回数量
        name: 任务名过滤（如 'screen' 'auto_rebalance' 'daily_run' 'backtest'）
        status: pending / running / done / failed
    """
    rows = task_mgr.list_tasks(limit=limit, name_filter=name, status_filter=status)
    return [t.to_dict(include_result=False) for t in rows]


@router.get("/history")
def get_history(name: Optional[str] = None, status: Optional[str] = None,
                limit: int = 30):
    """从 DB 归档表查任务历史（重启不丢，跨 API 进程可见）

    只有 done / failed 状态的任务会被归档；进行中的任务仍只在内存。
    """
    return task_mgr.list_history(name=name, status=status, limit=limit)


@router.delete("/cleanup")
def cleanup_tasks(keep: int = 100):
    """清理内存任务表，只保留最近 N 个（已归档的 DB 数据不动）"""
    n_removed = task_mgr.cleanup(keep=keep)
    return {"removed": n_removed, "kept": keep}


@router.get("/{task_id}")
def get_task(task_id: str, include_result: bool = True,
             include_traceback: bool = False):
    """查询单个任务（先查内存，没有就降级到 DB 归档）

    返回字段 `from_db`：
      - false → 任务在内存中（可能正在跑或刚跑完）
      - true  → 任务已归档到 DB（API 重启后内存丢失，但 DB 还有）

    pending: 已创建未开始（实际本框架启动即 running）
    running: 后台执行中（可看 progress / progress_msg）
    done:    完成（result 含返回数据）
    failed:  失败（error 含错误信息）
    """
    d = task_mgr.get_or_db(task_id, include_traceback=include_traceback)
    if d is None:
        raise NotFound(f"task_id {task_id} 不存在（内存和 DB 归档里都没有）",
                       code="TASK_NOT_FOUND")
    if not include_result:
        d.pop("result", None)
    return d
