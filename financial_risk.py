"""财务风险规则。

规则只使用已经落库的结构化财务字段。新闻、公告中的事件风险由日报模块
单独处理，避免全市场逐只请求外部接口。
"""
from typing import Any, Dict

import pandas as pd


FINANCIAL_INDUSTRY_KEYWORDS = (
    "银行", "保险", "证券", "多元金融", "金融服务", "信托",
)


def _number(value: Any):
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _annualize_roe(value: Any, report_date: Any):
    number = _number(value)
    parsed = pd.to_datetime(report_date, errors="coerce")
    if number is None or pd.isna(parsed):
        return number
    multiplier = {3: 4.0, 6: 2.0, 9: 4.0 / 3, 12: 1.0}.get(parsed.month, 1.0)
    return number * multiplier


def assess_financial_risk(row: dict) -> Dict[str, Any]:
    """评估单只股票最新财务风险。

    high 表示禁止进入候选池；medium 仅作为提示，不直接剔除。
    """
    net_profit = _number(row.get("net_profit"))
    profit_yoy = _number(row.get("net_profit_yoy"))
    revenue_yoy = _number(row.get("revenue_yoy"))
    roe = _number(row.get("roe"))
    prev_roe = _number(row.get("prev_roe"))
    debt_ratio = _number(row.get("debt_ratio"))
    industry = str(row.get("industry") or "")

    high_flags = []
    medium_flags = []

    if net_profit is not None and profit_yoy is not None:
        if net_profit < 0 and profit_yoy <= -30:
            high_flags.append("LOSS_AND_PROFIT_DROP")
    if profit_yoy is not None and profit_yoy <= -70:
        high_flags.append("PROFIT_DROP_70")
    if revenue_yoy is not None and revenue_yoy <= -40:
        high_flags.append("REVENUE_DROP_40")
    if (roe is not None and prev_roe is not None
            and roe < 0 and prev_roe < 0 and roe < prev_roe):
        high_flags.append("ROE_NEGATIVE_WORSENING")

    is_financial = any(keyword in industry for keyword in FINANCIAL_INDUSTRY_KEYWORDS)
    if debt_ratio is not None and debt_ratio >= 85 and not is_financial:
        if net_profit is not None and net_profit < 0:
            high_flags.append("HIGH_DEBT_WITH_LOSS")
        else:
            medium_flags.append("HIGH_DEBT_RATIO")

    if high_flags:
        level = "high"
    elif medium_flags:
        level = "medium"
    elif all(v is None for v in (net_profit, profit_yoy, revenue_yoy, roe, debt_ratio)):
        level = "unknown"
    else:
        level = "low"

    flags = list(dict.fromkeys(high_flags + medium_flags))
    return {
        "eligible": level != "high",
        "financial_risk_level": level,
        "financial_risk_flags": flags,
    }


def attach_financial_risk(universe: pd.DataFrame,
                          financial: pd.DataFrame) -> pd.DataFrame:
    """把最新财务字段和风险结论挂到股票池。"""
    base = universe.copy()
    if financial is not None and not financial.empty:
        fin = financial.drop_duplicates("ts_code", keep="last").copy()
        base = base.merge(fin, on="ts_code", how="left", suffixes=("", "_fin"))

    assessments = base.apply(lambda row: assess_financial_risk(row.to_dict()), axis=1)
    assessed = pd.DataFrame(assessments.tolist(), index=base.index)
    for col in assessed.columns:
        base[col] = assessed[col]
    return base


def assess_stock_eligibility(ts_code: str) -> Dict[str, Any]:
    """直接从数据库检查持仓股票是否仍满足硬准入规则。"""
    from data.db import get_conn

    with get_conn() as conn:
        row = pd.read_sql(
            "SELECT b.ts_code, b.name, b.industry, b.is_active, b.is_st, "
            "f.report_date, f.roe, f.net_profit, f.net_profit_yoy, "
            "f.revenue_yoy, f.debt_ratio, "
            "(SELECT p.roe FROM market_financial p "
            " WHERE p.ts_code=f.ts_code AND p.report_date<f.report_date "
            " ORDER BY p.report_date DESC LIMIT 1) AS prev_roe, "
            "(SELECT p.report_date FROM market_financial p "
            " WHERE p.ts_code=f.ts_code AND p.report_date<f.report_date "
            " ORDER BY p.report_date DESC LIMIT 1) AS prev_report_date "
            "FROM market_stock_basic b "
            "LEFT JOIN market_financial f ON f.ts_code=b.ts_code "
            "AND f.report_date=(SELECT MAX(x.report_date) FROM market_financial x "
            "                   WHERE x.ts_code=b.ts_code) "
            "WHERE b.ts_code=%s LIMIT 1",
            conn, params=(ts_code,))
    if row.empty:
        return {
            "eligible": False,
            "financial_risk_level": "high",
            "financial_risk_flags": ["STOCK_BASIC_MISSING"],
        }
    values = row.iloc[0].to_dict()
    values["roe"] = _annualize_roe(values.get("roe"), values.get("report_date"))
    values["prev_roe"] = _annualize_roe(
        values.get("prev_roe"), values.get("prev_report_date"))
    result = assess_financial_risk(values)
    name = str(values.get("name") or "").upper()
    active = _number(values.get("is_active"))
    st_value = _number(values.get("is_st"))
    if active != 1:
        result["eligible"] = False
        result["financial_risk_level"] = "high"
        result["financial_risk_flags"].append("INACTIVE_STOCK")
    is_st = st_value == 1 or "ST" in name or "退" in name
    if is_st:
        result["eligible"] = False
        result["financial_risk_level"] = "high"
        result["financial_risk_flags"].append("ST_OR_DELIST_RISK")
    result["financial_risk_flags"] = list(dict.fromkeys(result["financial_risk_flags"]))
    return result
