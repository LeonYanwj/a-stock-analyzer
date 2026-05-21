"""FastAPI 主入口

启动:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

文档:
    http://localhost:8000/docs        交互式 Swagger
    http://localhost:8000/redoc       ReDoc
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import accounts, screen, rate, backtest, stocks


app = FastAPI(
    title="A 股量化系统 API",
    description="模拟盘、选股、评级、回测、数据查询",
    version="0.1.0",
)

# CORS：允许所有来源（初期调试用，生产环境收紧）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 注册路由
app.include_router(accounts.router)
app.include_router(screen.router)
app.include_router(rate.router)
app.include_router(backtest.router)
app.include_router(stocks.router)


@app.get("/")
def root():
    return {
        "name": "A 股量化系统 API",
        "version": "0.1.0",
        "docs": "/docs",
        "endpoints": {
            "accounts":  "/api/accounts",
            "screen":    "/api/screen",
            "rate":      "/api/rate/{code}",
            "backtest":  "/api/backtest",
            "stocks":    "/api/stocks",
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
