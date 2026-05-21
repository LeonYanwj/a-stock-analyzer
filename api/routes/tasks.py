"""任务管理 API（查询任务状态/进度/结果）"""
from typing import Optional
from fastapi import APIRouter, HTTPException

from api import tasks as task_mgr


router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
def list_tasks(limit: int = 30, name: Optional[str] = None,
               status: Optional[str] = None):
    """列出任务（最近 N 个），可按 name 或 status 过滤

    Args:
        limit: 返回数量
        name: 任务名过滤（如 'screen' 'auto_rebalance' 'backtest'）
        status: pending / running / done / failed
    """
    rows = task_mgr.list_tasks(limit=limit, name_filter=name, status_filter=status)
    return [t.to_dict(include_result=False) for t in rows]


@router.get("/{task_id}")
def get_task(task_id: str, include_result: bool = True,
             include_traceback: bool = False):
    """查询单个任务

    pending: 已创建未开始（实际本框架启动即 running）
    running: 后台执行中（可看 progress / progress_msg）
    done:    完成（result 含返回数据）
    failed:  失败（error 含错误信息）
    """
    t = task_mgr.get(task_id)
    if t is None:
        raise HTTPException(404, f"task_id {task_id} 不存在（重启后任务表会清空）")
    return t.to_dict(include_result=include_result,
                    include_traceback=include_traceback)


@router.delete("/cleanup")
def cleanup_tasks(keep: int = 100):
    """清理旧任务，只保留最近 N 个"""
    n_removed = task_mgr.cleanup(keep=keep)
    return {"removed": n_removed, "kept": keep}
