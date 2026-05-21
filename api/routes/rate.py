"""单股评级 API"""
from datetime import datetime, timedelta
from fastapi import APIRouter

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
    """单股 5 维度评级"""
    if strategy not in list_strategies():
        raise BadRequest(f"未知策略：{strategy}", code="UNKNOWN_STRATEGY",
                         detail=f"可选：{list_strategies()}")
    rating = _do_rate(code, strategy, no_flow, no_news)

    # 转 JSON
    dims_out = []
    for d in rating.dimensions:
        dims_out.append({
            "key": d.key,
            "label": d.label,
            "stars": d.stars,
            "weight": rating.dim_weights.get(d.key, 1.0),
            "factors": [
                {"key": f.key, "stars": f.stars, "desc": f.desc}
                for f in d.factors
            ],
        })
    return {
        "ts_code": rating.ts_code,
        "name": rating.name,
        "asof": rating.asof,
        "strategy": rating.strategy,
        "overall_stars": rating.overall_stars,
        "grade": rating.grade,
        "dimensions": dims_out,
    }
