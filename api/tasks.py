"""简单内存任务队列（基于 threading）+ DB 归档

设计：
- 进行中的任务在内存（pending/running/done/failed），轮询低延迟
- done / failed 任务自动写进 api_task_history 表（重启不丢）
- GET /api/tasks/{id} 内存里没找到时降级到 DB 查
- GET /api/tasks/history 直接查 DB

不适合：
- 多进程部署（每个进程内存任务表独立）
- 高并发（无锁竞争控制）

需要这些时升级 celery / arq + Redis。
"""
import json
import uuid
import logging
import math
import threading
import traceback
from datetime import datetime
from typing import Callable, Dict, Any, Optional


logger = logging.getLogger("api.tasks")


class Task:
    """单个后台任务"""

    def __init__(self, name: str, fn: Callable, args: tuple = (), kwargs: dict = None,
                 params: dict = None):
        self.task_id: str = str(uuid.uuid4())
        self.name: str = name
        self.status: str = "pending"           # pending / running / done / failed
        self.progress: int = 0                  # 0-100
        self.progress_msg: str = ""             # 当前阶段描述
        self.result: Any = None                 # 任务返回值
        self.error: Optional[str] = None
        self.traceback: Optional[str] = None
        self.created_at: datetime = datetime.now()
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None
        self.params: dict = params or {}        # 入参快照（用于 DB 归档 + 复盘）
        self.progress_events: list = []        # 当前进程内的阶段时间线，供详情页展示
        self._fn = fn
        self._args = args
        self._kwargs = kwargs or {}
        self._thread: Optional[threading.Thread] = None

    def report(self, progress: int, msg: str = ""):
        """任务函数可以调用 task.report() 更新进度"""
        self.progress = max(0, min(100, progress))
        if msg:
            self.progress_msg = msg
            self.progress_events.append({
                "progress": self.progress,
                "message": msg,
                "at": datetime.now().isoformat(),
            })

    def start(self):
        self.status = "running"
        self.started_at = datetime.now()
        self.report(0, "任务已进入后台队列")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            # 如果函数签名第一参数是 'task'，自动注入 self（用于进度报告）
            import inspect
            sig = inspect.signature(self._fn)
            params = list(sig.parameters.keys())
            if params and params[0] == "task":
                result = self._fn(self, *self._args, **self._kwargs)
            else:
                result = self._fn(*self._args, **self._kwargs)
            self.result = result
            self.status = "done"
            self.progress = 100
            self.progress_msg = "completed"
        except Exception as e:
            self.status = "failed"
            self.error = f"{type(e).__name__}: {e}"
            self.traceback = traceback.format_exc()
            self.progress_msg = "failed"
        finally:
            self.finished_at = datetime.now()
            # 完成的任务自动归档进 DB（失败不抛，避免影响主流程）
            self._archive_to_db()

    def _archive_to_db(self):
        """把 done/failed 任务写进 api_task_history。DB 出问题不抛。"""
        try:
            from data.db import get_conn, insert_api_task
            with get_conn() as conn:
                insert_api_task(conn, self.to_dict_db())
        except Exception as e:
            logger.warning(
                "[task %s] DB 归档失败 %s: %s",
                self.task_id[:8], type(e).__name__, e,
            )

    def to_dict_db(self) -> dict:
        """DB 归档用的字典（含 params / traceback）"""
        return {
            "task_id": self.task_id,
            "name": self.name,
            "status": self.status,
            "progress_msg": self.progress_msg,
            "params": self.params,
            "result": self.result,
            "error": self.error,
            "traceback": self.traceback,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "started_at": self.started_at.strftime("%Y-%m-%d %H:%M:%S") if self.started_at else None,
            "finished_at": self.finished_at.strftime("%Y-%m-%d %H:%M:%S") if self.finished_at else None,
            "duration_seconds": self.duration_seconds,
        }

    @property
    def duration_seconds(self) -> Optional[float]:
        if not self.started_at:
            return None
        end = self.finished_at or datetime.now()
        return (end - self.started_at).total_seconds()

    def to_dict(self, include_result: bool = True,
                include_traceback: bool = False) -> dict:
        d = {
            "task_id": self.task_id,
            "name": self.name,
            "status": self.status,
            "progress": self.progress,
            "progress_msg": self.progress_msg,
            "params": self.params,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
            "from_db": False,
        }
        if include_result:
            d["result"] = self.result
        d["progress_events"] = list(self.progress_events)
        if include_traceback and self.traceback:
            d["traceback"] = self.traceback
        return d


def serialize_history_task_row(row) -> dict:
    """将 Pandas 读取的归档任务行转换为严格 JSON 可序列化的字典。

    MySQL 的可空 ``duration_seconds`` 经 Pandas 读取后会变成 ``NaN``，而
    Starlette 的 JSON 响应会拒绝 NaN/Infinity。对前端而言，未知时长应表示为
    JSON ``null``，而不是让整个任务列表返回 500。
    """
    d = row.to_dict()
    if d.get("params") and isinstance(d["params"], str):
        try:
            d["params"] = json.loads(d["params"])
        except json.JSONDecodeError:
            pass
    for key in ("created_at", "started_at", "finished_at"):
        if d.get(key) is not None and hasattr(d[key], "isoformat"):
            d[key] = d[key].isoformat()
    duration = d.get("duration_seconds")
    if duration is not None:
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            duration = None
        d["duration_seconds"] = duration if duration is not None and math.isfinite(duration) else None
    return d


# 全局任务表（进程级，每次启动清空）
_TASKS: Dict[str, Task] = {}
_LOCK = threading.Lock()


def submit(name: str, fn: Callable, *args, params: dict = None, **kwargs) -> Task:
    """提交一个任务到后台跑，立即返回 Task 对象

    Args:
        name: 任务类别名 (screen / auto_rebalance / daily_run / backtest)
        fn:   实际执行的函数（可选地接受 task 作为首参以报告进度）
        *args, **kwargs: 透传给 fn 的入参
        params: 入参快照（DB 归档用，用于历史复盘时知道是什么参数跑的）
    """
    task = Task(name, fn, args, kwargs, params=params)
    with _LOCK:
        _TASKS[task.task_id] = task
    task.start()
    return task


def get(task_id: str) -> Optional[Task]:
    """从内存任务表里查 task；返回 None 表示内存里没有（可能在 DB 归档里）"""
    with _LOCK:
        return _TASKS.get(task_id)


def get_or_db(task_id: str, include_traceback: bool = False) -> Optional[dict]:
    """先查内存；内存没有就降级到 DB 归档查。返回 dict 或 None"""
    t = get(task_id)
    if t is not None:
        return t.to_dict(include_result=True, include_traceback=include_traceback)
    # 内存没找到，查 DB
    try:
        from data.db import get_conn, get_api_task
        with get_conn() as conn:
            return get_api_task(conn, task_id)
    except Exception as e:
        logger.warning("[task %s] DB fallback 查询失败: %s", task_id[:8], e)
        return None


def list_history(name: str = None, status: str = None, limit: int = 30) -> list:
    """查归档任务列表（DB），用于 GET /api/tasks/history"""
    try:
        from data.db import get_conn, list_api_tasks
        with get_conn() as conn:
            df = list_api_tasks(conn, name=name, status=status, limit=limit)
        if df.empty:
            return []
        # 把 datetime / decimal 转 JSON 友好
        rows = []
        for _, r in df.iterrows():
            rows.append(serialize_history_task_row(r))
        return rows
    except Exception as e:
        logger.warning("[history] DB 查询失败: %s", e)
        return []


def list_tasks(limit: int = 50, name_filter: str = None,
               status_filter: str = None) -> list:
    """列出最近 N 个任务（按创建时间倒序）"""
    with _LOCK:
        tasks = list(_TASKS.values())
    if name_filter:
        tasks = [t for t in tasks if t.name == name_filter]
    if status_filter:
        tasks = [t for t in tasks if t.status == status_filter]
    tasks.sort(key=lambda t: t.created_at, reverse=True)
    return tasks[:limit]


def cleanup(keep: int = 100):
    """清理任务表，只保留最近 N 个（避免内存爆）"""
    with _LOCK:
        if len(_TASKS) <= keep:
            return 0
        sorted_tasks = sorted(_TASKS.values(), key=lambda t: t.created_at, reverse=True)
        to_keep = {t.task_id for t in sorted_tasks[:keep]}
        removed = []
        for tid in list(_TASKS.keys()):
            if tid not in to_keep:
                removed.append(tid)
                del _TASKS[tid]
        return len(removed)
