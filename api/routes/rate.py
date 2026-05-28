"""单股评级 API（同步 + SSE 流式）"""
import json
import traceback
from datetime import datetime, timedelta
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from api.errors import NotFound, BadRequest

import pandas as pd
import numpy as np

from data.fetcher import DataFetcher
from single_grader import grade_single
from news_scorer import compute_news_score
from strategies import get_dim_weights, list_strategies
from pattern_recognizer import compute_pattern_score, list_patterns
from rate import normalize_code, compute_tech_factors


router = APIRouter(prefix="/api/rate", tags=["rate"])


def _rating_to_dict(rating) -> dict:
    """把 grade_single 返回对象转 JSON 友好结构（同步/流式共用）"""
    dims = []
    for d in rating.dimensions:
        dims.append({
            "key": d.key, "label": d.label, "stars": d.stars,
            "weight": rating.dim_weights.get(d.key, 1.0),
            "factors": [{"key": f.key, "stars": f.stars, "desc": f.desc}
                        for f in d.factors],
        })
    return {
        "ts_code": rating.ts_code, "name": rating.name, "asof": rating.asof,
        "strategy": rating.strategy,
        "overall_stars": rating.overall_stars, "grade": rating.grade,
        "dimensions": dims,
    }


def _do_rate(code: str, strategy: str, no_flow: bool, no_news: bool,
             lookback: int = 90):
    """复用 rate.py 的核心逻辑"""
    ts_code = normalize_code(code)
    fetcher = DataFetcher()
    asof_dt = datetime.now()
    asof = asof_dt.strftime("%Y%m%d")
    start = (asof_dt - timedelta(days=lookback + 30)).strftime("%Y%m%d")

    # 1. 历史日线
    daily = fetcher.get_daily(ts_code, start, asof)
    if daily.empty:
        raise NotFound(f"{ts_code} 历史数据为空", code="STOCK_DATA_EMPTY")

    factor_values = compute_tech_factors(daily)
    factor_values["pattern_score"] = compute_pattern_score(daily, lookback=5)

    # 2a. 估值
    ind = fetcher.get_stock_indicator(ts_code)
    if not ind.empty:
        last = ind.iloc[-1]
        for src, dst in [("pe_ttm", "pe_ttm"), ("pe", "pe_ttm"), ("pb", "pb")]:
            if src in last and pd.notna(last[src]) and dst not in factor_values:
                factor_values[dst] = float(last[src])

    # 2b. 名称
    name = ""
    try:
        spot = fetcher.get_market_snapshot()
        row = spot[spot["ts_code"] == ts_code]
        if not row.empty:
            name = row.iloc[0].get("name", "") or ""
    except Exception:
        pass

    # 2c. 财务
    fin = fetcher.get_stock_financial_abstract(ts_code)
    if not fin.empty:
        last = fin.iloc[-1]
        rpt = last.get("报告期")
        month = None
        if hasattr(rpt, "month"):
            month = rpt.month
        elif isinstance(rpt, str) and len(rpt) >= 7 and rpt[4] == "-":
            try:    month = int(rpt[5:7])
            except: pass
        annualize = {3: 4.0, 6: 2.0, 9: 4.0 / 3, 12: 1.0}.get(month, 1.0)
        if "净资产收益率" in last and pd.notna(last["净资产收益率"]):
            factor_values["roe"] = float(last["净资产收益率"]) * annualize
        if "销售毛利率" in last and pd.notna(last["销售毛利率"]):
            factor_values["gross_margin"] = float(last["销售毛利率"])

    # 3. 资金流
    if not no_flow:
        try:
            ff = fetcher.get_fund_flow_snapshot(window="5日排行")
            if not ff.empty:
                row = ff[ff["ts_code"] == ts_code]
                if not row.empty:
                    r = row.iloc[0]
                    if "fund_net" in r and pd.notna(r["fund_net"]):
                        factor_values["fund_net_5d"] = float(r["fund_net"])
                    if {"fund_inflow", "fund_outflow"}.issubset(r.index):
                        inflow = float(r["fund_inflow"]) if pd.notna(r["fund_inflow"]) else 0
                        outflow = float(r["fund_outflow"]) if pd.notna(r["fund_outflow"]) else 0
                        total = abs(inflow) + abs(outflow)
                        if total > 0:
                            factor_values["inflow_ratio_5d"] = (inflow - outflow) / total
        except Exception:
            pass

    # 4. 消息面
    if not no_news:
        try:
            ns = compute_news_score(
                fetcher.get_stock_news(ts_code),
                fetcher.get_stock_disclosure(ts_code, days=30),
                fetcher.get_stock_research(ts_code))
            if not pd.isna(ns["news_score"]):
                factor_values["news_score"] = ns["news_score"]
        except Exception:
            pass

    # 5. 评级
    dim_weights = get_dim_weights(strategy)
    rating = grade_single(ts_code=ts_code, name=name, asof=asof,
                          factor_values=factor_values, dim_weights=dim_weights,
                          strategy=strategy)
    return rating


@router.get("/{code}")
def rate_stock(code: str, strategy: str = "swing",
               no_flow: bool = False, no_news: bool = False):
    """【同步】单股 5 维度评级（一次性返回，3-10 秒阻塞）"""
    if strategy not in list_strategies():
        raise BadRequest(f"未知策略：{strategy}", code="UNKNOWN_STRATEGY",
                         detail=f"可选：{list_strategies()}")
    rating = _do_rate(code, strategy, no_flow, no_news)
    return _rating_to_dict(rating)


# -------------------- SSE 流式评级 --------------------
def _sse(obj: dict) -> str:
    """SSE 行格式化：data: <json>\\n\\n"""
    return f"data: {json.dumps(obj, ensure_ascii=False, default=str)}\n\n"


def _rate_stream_gen(code: str, strategy: str, no_flow: bool, no_news: bool,
                     lookback: int = 90):
    """生成器：边算边推 SSE 事件

    事件 schema:
      - {"progress": int, "msg": str}        进度更新
      - {"progress": 100, "result": {...}}   最终结果
      - {"error": str, "message": str}       失败
    """
    try:
        yield _sse({"progress": 3, "msg": "解析股票代码..."})
        ts_code = normalize_code(code)
        fetcher = DataFetcher()
        asof_dt = datetime.now()
        asof = asof_dt.strftime("%Y%m%d")
        start = (asof_dt - timedelta(days=lookback + 30)).strftime("%Y%m%d")

        yield _sse({"progress": 15, "msg": f"拉历史日线 {ts_code}..."})
        daily = fetcher.get_daily(ts_code, start, asof)
        if daily.empty:
            yield _sse({"error": "STOCK_DATA_EMPTY",
                        "message": f"{ts_code} 历史数据为空"})
            return

        yield _sse({"progress": 30, "msg": "计算量价因子..."})
        factor_values = compute_tech_factors(daily)
        factor_values["pattern_score"] = compute_pattern_score(daily, lookback=5)

        yield _sse({"progress": 40, "msg": "拉估值数据 (PE/PB)..."})
        ind = fetcher.get_stock_indicator(ts_code)
        if not ind.empty:
            last = ind.iloc[-1]
            for src, dst in [("pe_ttm", "pe_ttm"), ("pe", "pe_ttm"), ("pb", "pb")]:
                if src in last and pd.notna(last[src]) and dst not in factor_values:
                    factor_values[dst] = float(last[src])

        yield _sse({"progress": 50, "msg": "拉名称 spot..."})
        name = ""
        try:
            spot = fetcher.get_market_snapshot()
            row = spot[spot["ts_code"] == ts_code]
            if not row.empty:
                name = row.iloc[0].get("name", "") or ""
        except Exception:
            pass

        yield _sse({"progress": 60, "msg": "拉财务摘要 (ROE/毛利)..."})
        fin = fetcher.get_stock_financial_abstract(ts_code)
        if not fin.empty:
            last = fin.iloc[-1]
            rpt = last.get("报告期")
            month = None
            if hasattr(rpt, "month"):
                month = rpt.month
            elif isinstance(rpt, str) and len(rpt) >= 7 and rpt[4] == "-":
                try:    month = int(rpt[5:7])
                except: pass
            annualize = {3: 4.0, 6: 2.0, 9: 4.0 / 3, 12: 1.0}.get(month, 1.0)
            if "净资产收益率" in last and pd.notna(last["净资产收益率"]):
                factor_values["roe"] = float(last["净资产收益率"]) * annualize
            if "销售毛利率" in last and pd.notna(last["销售毛利率"]):
                factor_values["gross_margin"] = float(last["销售毛利率"])

        if not no_flow:
            yield _sse({"progress": 75, "msg": "拉资金流..."})
            try:
                ff = fetcher.get_fund_flow_snapshot(window="5日排行")
                if not ff.empty:
                    row = ff[ff["ts_code"] == ts_code]
                    if not row.empty:
                        r = row.iloc[0]
                        if "fund_net" in r and pd.notna(r["fund_net"]):
                            factor_values["fund_net_5d"] = float(r["fund_net"])
                        if {"fund_inflow", "fund_outflow"}.issubset(r.index):
                            inflow = float(r["fund_inflow"]) if pd.notna(r["fund_inflow"]) else 0
                            outflow = float(r["fund_outflow"]) if pd.notna(r["fund_outflow"]) else 0
                            total = abs(inflow) + abs(outflow)
                            if total > 0:
                                factor_values["inflow_ratio_5d"] = (inflow - outflow) / total
            except Exception:
                pass

        if not no_news:
            yield _sse({"progress": 85, "msg": "拉消息面（公告/新闻/研报）..."})
            try:
                ns = compute_news_score(
                    fetcher.get_stock_news(ts_code),
                    fetcher.get_stock_disclosure(ts_code, days=30),
                    fetcher.get_stock_research(ts_code))
                if not pd.isna(ns["news_score"]):
                    factor_values["news_score"] = ns["news_score"]
            except Exception:
                pass

        yield _sse({"progress": 95, "msg": "计算 5 维度评级..."})
        dim_weights = get_dim_weights(strategy)
        rating = grade_single(ts_code=ts_code, name=name, asof=asof,
                              factor_values=factor_values, dim_weights=dim_weights,
                              strategy=strategy)

        yield _sse({"progress": 100, "result": _rating_to_dict(rating)})

    except Exception as e:
        yield _sse({
            "error": type(e).__name__,
            "message": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc()[-500:],
        })


@router.get("/{code}/stream")
def rate_stock_stream(code: str, strategy: str = "swing",
                      no_flow: bool = False, no_news: bool = False):
    """【SSE 流式】单股 5 维度评级 - 实时推送进度，最终推送结果

    一个 HTTP 连接，分阶段推送 8-10 个 SSE 事件：
      data: {"progress": 30, "msg": "计算量价因子..."}
      data: {"progress": 50, "msg": "拉名称 spot..."}
      ...
      data: {"progress": 100, "result": {...完整评级...}}

    前端用法（JS EventSource）：
      const ev = new EventSource('/api/rate/600519/stream?strategy=swing');
      ev.onmessage = (e) => {
        const d = JSON.parse(e.data);
        if (d.result)  { showResult(d.result); ev.close(); }
        if (d.error)   { showError(d.message); ev.close(); }
        if (d.progress != null) updateBar(d.progress, d.msg);
      };

    命令行测试：
      curl -N "http://localhost:8000/api/rate/600519/stream?strategy=swing"
    """
    if strategy not in list_strategies():
        raise BadRequest(f"未知策略：{strategy}", code="UNKNOWN_STRATEGY",
                         detail=f"可选：{list_strategies()}")
    return StreamingResponse(
        _rate_stream_gen(code, strategy, no_flow, no_news),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 让 nginx 不要缓冲
        },
    )
