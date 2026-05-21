"""简单内存任务队列（基于 threading）

适合个人系统使用：
- 任务存内存（重启丢失）
- 任意 Python 函数能丢进来后台跑
- 前端轮询 GET /api/tasks/{task_id} 查状态

不适合：
- 多进程部署（每个进程独立任务表）
- 任务需要持久化（重启不丢）
- 高并发（无锁竞争控制）

需要这些时升级 celery / arq + Redis。
"""
import uuid
import threading
import traceback
from datetime import datetime
from typing import Callable, Dict, Any, Optional


class Task:
    """单个后台任务"""

    def __init__(self, name: str, fn: Callable, args: tuple = (), kwargs: dict = None):
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
        self._fn = fn
        self._args = args
        self._kwargs = kwargs or {}
        self._thread: Optional[threading.Thread] = None

    def report(self, progress: int, msg: str = ""):
        """任务函数可以调用 task.report() 更新进度"""
        self.progress = max(0, min(100, progress))
        if msg:
            self.progress_msg = msg

    def start(self):
        self.status = "running"
        self.started_at = datetime.now()
        self.progress_msg = "started"
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
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
        }
        if include_result:
            d["result"] = self.result
        if include_traceback and self.traceback:
            d["traceback"] = self.traceback
        return d


# 全局任务表（进程级，每次启动清空）
_TASKS: Dict[str, Task] = {}
_LOCK = threading.Lock()


def submit(name: str, fn: Callable, *args, **kwargs) -> Task:
    """提交一个任务到后台跑，立即返回 Task 对象"""
    task = Task(name, fn, args, kwargs)
    with _LOCK:
        _TASKS[task.task_id] = task
    task.start()
    return task


def get(task_id: str) -> Optional[Task]:
    with _LOCK:
        return _TASKS.get(task_id)


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
