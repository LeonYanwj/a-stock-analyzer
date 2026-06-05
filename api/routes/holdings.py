"""实盘持仓接口 + 盘后全方位分析（手动触发 SSE）"""
import json
import traceback
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import real_holding as rh
from api.errors import NotFound, BadRequest

router = APIRouter(prefix="/api/holdings", tags=["holdings"])


class HoldingIn(BaseModel):
    code: str
    qty: int
    cost: float
    buy_date: Optional[str] = None
    name: Optional[str] = None
    note: Optional[str] = None


class HoldingUpdate(BaseModel):
    qty: Optional[int] = None
    cost: Optional[float] = None
    buy_date: Optional[str] = None
    name: Optional[str] = None
    note: Optional[str] = None


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False, default=str)}\n\n"


# -------------------- 分析（SSE，放在 /{holding_id} 之前避免被吞）--------------------
def _analyze_stream_gen(strategy: str, send: bool):
    try:
        import holding_analyzer as ha
        from data.fetcher import DataFetcher
        from datetime import date

        holdings = rh.list_holdings()
        if holdings.empty:
            yield _sse({"error": "NO_HOLDINGS", "message": "还没有持仓，请先录入"})
            return

        rows = holdings.to_dict("records")
        n = len(rows)
        fetcher = DataFetcher()
        analyses = []
        for i, h in enumerate(rows, 1):
            yield _sse({"progress": int(i / n * 80),
                        "msg": f"分析 {h['ts_code']} {h.get('name','')} ({i}/{n})..."})
            analyses.append(ha.analyze_one(h, strategy, fetcher))

        yield _sse({"progress": 85, "msg": "生成报告..."})
        asof = date.today().isoformat()
        html = ha.build_report_html(analyses, asof)

        mail = None
        if send:
            yield _sse({"progress": 92, "msg": "发送邮件..."})
            import notify
            mail = notify.send_mail(f"持仓盘后分析 · {asof}（{n}只）", html)

        yield _sse({"progress": 100,
                    "result": {"count": n, "asof": asof, "mail": mail, "analyses": analyses}})
    except Exception as e:
        yield _sse({"error": type(e).__name__, "message": str(e),
                    "traceback": traceback.format_exc()[-500:]})


@router.get("/analyze/stream")
def analyze_stream(strategy: str = "swing", send: bool = True):
    """【SSE】手动触发盘后全方位分析（调外网，分阶段推进度）

    事件：{progress,msg} 进度 / {progress:100,result:{...}} 完成 / {error,message} 失败
    send=true 时同时发邮件（需先配好 /api/notify/config）。
    """
    return StreamingResponse(
        _analyze_stream_gen(strategy, send),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# -------------------- 持仓 CRUD --------------------
@router.get("")
def list_holdings():
    """实盘持仓列表"""
    df = rh.list_holdings()
    return df.to_dict("records") if not df.empty else []


@router.post("")
def add_holding(body: HoldingIn):
    """新增/更新一只持仓（同代码已存在则覆盖）"""
    try:
        hid = rh.add_holding(body.code, body.qty, body.cost,
                             body.buy_date, body.name, body.note)
    except Exception as e:
        raise BadRequest(f"录入失败: {type(e).__name__}: {e}", code="HOLDING_ADD_FAILED")
    return {"ok": True, "holding_id": hid}


@router.put("/{holding_id}")
def update_holding(holding_id: int, body: HoldingUpdate):
    """修改持仓（部分字段）"""
    if rh.get_holding(holding_id) is None:
        raise NotFound(f"持仓 {holding_id} 不存在", code="HOLDING_NOT_FOUND")
    rh.update_holding(holding_id, qty=body.qty, cost=body.cost,
                      buy_date=body.buy_date, name=body.name, note=body.note)
    return {"ok": True, "holding_id": holding_id}


@router.delete("/{holding_id}")
def delete_holding(holding_id: int):
    """删除持仓"""
    if not rh.delete_holding(holding_id):
        raise NotFound(f"持仓 {holding_id} 不存在", code="HOLDING_NOT_FOUND")
    return {"ok": True, "deleted": holding_id}
