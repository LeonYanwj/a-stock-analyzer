"""FastAPI 主入口

启动:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

文档:
    http://localhost:8000/docs        交互式 Swagger
    http://localhost:8000/redoc       ReDoc
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import errors as api_errors
from api import scheduler as sched
from api.routes import accounts, screen, rate, backtest, stocks, tasks, scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 进程启动：拉起定时任务（每交易日傍晚更新行情 + daily_runner）
    sched.start_scheduler()
    yield
    # 进程关闭：优雅停掉调度器
    sched.shutdown_scheduler()


app = FastAPI(
    title="A 股量化系统 API",
    description="模拟盘、选股、评级、回测、数据查询",
    version="0.2.0",
    lifespan=lifespan,
)

# CORS：允许所有来源（初期调试用，生产环境收紧）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 统一错误处理（所有异常 → 结构化 JSON）
api_errors.install(app)


# 注册路由
app.include_router(accounts.router)
app.include_router(screen.router)
app.include_router(rate.router)
app.include_router(backtest.router)
app.include_router(stocks.router)
app.include_router(tasks.router)
app.include_router(scheduler.router)


@app.get("/")
def root():
    return {
        "name": "A 股量化系统 API",
        "version": "0.2.0",
        "docs": "/docs",
        "endpoints": {
            "accounts":  "/api/accounts  (含 POST /{id}/auto-rebalance/async 和 daily-run/async)",
            "screen":    "/api/screen (同步) 或 POST /api/screen/async (异步)",
            "rate":      "/api/rate/{code}",
            "backtest":  "/api/backtest (含 POST /run/async 触发回测)",
            "stocks":    "/api/stocks",
            "tasks":     "/api/tasks (异步任务查询)",
            "scheduler": "/api/scheduler/status (定时任务状态) 或 POST /run-now 手动触发",
        }
    }


@app.get("/health")
def health():
    """健康检查（顺便测 DB 连通）"""
    try:
        from data.db import get_conn
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close()
        return {"status": "ok", "db": "ok"}
    except Exception as e:
        return {"status": "degraded", "db": f"{type(e).__name__}: {e}"}
