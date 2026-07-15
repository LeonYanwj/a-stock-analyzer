"""自选股每日信息汇总与邮件报告。"""
from datetime import date, timedelta
from html import escape

import pandas as pd

from data.db import get_conn
from data.fetcher import DataFetcher
from news_scorer import compute_news_score
from rating_store import get_latest_rating, get_previous_rating
from financial_risk import assess_stock_eligibility
import notify
import watchlist


def _latest_prices(ts_code: str) -> dict:
    with get_conn() as conn:
        df = pd.read_sql(
            "SELECT trade_date,close,pct_chg FROM market_daily WHERE ts_code=%s "
            "AND adjust='qfq' ORDER BY trade_date DESC LIMIT 2",
            conn, params=(ts_code,))
    if df.empty:
        return {"trade_date": None, "close": None, "pct_chg": None}
    current = df.iloc[0]
    pct = current.get("pct_chg")
    if (pct is None or pd.isna(pct)) and len(df) > 1 and float(df.iloc[1]["close"]) > 0:
        pct = (float(current["close"]) / float(df.iloc[1]["close"]) - 1) * 100
    return {
        "trade_date": current["trade_date"],
        "close": float(current["close"]) if pd.notna(current["close"]) else None,
        "pct_chg": float(pct) if pct is not None and pd.notna(pct) else None,
    }


def _text(value, default="") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    return str(value)


def _titles(df: pd.DataFrame, column: str, limit: int = 3) -> list:
    if df is None or df.empty or column not in df.columns:
        return []
    return [str(v) for v in df[column].dropna().astype(str).head(limit).tolist()]


def _recent(df: pd.DataFrame, date_column: str, days: int = 2) -> pd.DataFrame:
    if df is None or df.empty or date_column not in df.columns:
        return pd.DataFrame() if df is None else df
    values = df.copy()
    values[date_column] = pd.to_datetime(values[date_column], errors="coerce")
    cutoff = pd.Timestamp(date.today() - timedelta(days=days))
    return values[values[date_column] >= cutoff]


def analyze(send: bool = True) -> dict:
    stocks = watchlist.list_all(active_only=True)
    if stocks.empty:
        return {"ok": False, "reason": "watchlist_empty", "count": 0}

    # 自选股即使没有模拟账户，也必须确保对应策略当天有全市场评级。
    today = date.today()
    for strategy in sorted(stocks["strategy"].dropna().unique().tolist()):
        sample_code = stocks[stocks["strategy"] == strategy].iloc[0]["ts_code"]
        latest = get_latest_rating(sample_code, strategy, today)
        latest_date = (pd.to_datetime(latest.get("trade_date")).date()
                       if latest and latest.get("trade_date") else None)
        if latest_date != today:
            from screen import screen_market
            screen_market(strategy=strategy, limit=0, verbose=False,
                          return_all=True, persist_ratings=True)

    fetcher = DataFetcher()
    analyses = []
    for _, item in stocks.iterrows():
        tc = item["ts_code"]
        strategy = _text(item.get("strategy"), "swing")
        current = get_latest_rating(tc, strategy, today)
        previous = (get_previous_rating(tc, strategy, current["trade_date"])
                    if current else None)
        eligibility = assess_stock_eligibility(tc)
        price = _latest_prices(tc)

        news = fetcher.get_stock_news(tc)
        disclosures = fetcher.get_stock_disclosure(tc, days=14)
        research = fetcher.get_stock_research(tc)
        news = _recent(news, "发布时间", days=2)
        disclosures = _recent(disclosures, "公告时间", days=2)
        research = _recent(research, "日期", days=2)
        sentiment = compute_news_score(news, disclosures, research)

        analyses.append({
            "ts_code": tc,
            "name": _text(item.get("name")),
            "group_name": _text(item.get("group_name"), "默认"),
            "strategy": strategy,
            "note": _text(item.get("note")),
            "rating": current,
            "previous": previous,
            "eligibility": eligibility,
            "price": price,
            "sentiment": sentiment,
            "news": _titles(news, "新闻标题"),
            "disclosures": _titles(disclosures, "公告标题"),
            "research": _titles(research, "报告名称"),
        })

    html = build_html(analyses, today.isoformat())
    mail = None
    if send:
        mail = notify.send_mail(f"自选股每日汇总 · {today.isoformat()}（{len(analyses)}只）", html)
    return {"ok": True, "count": len(analyses), "mail": mail, "html": html}


def _fmt(value, pattern="{:.2f}", fallback="-"):
    try:
        if value is None or pd.isna(value):
            return fallback
        return pattern.format(float(value))
    except (TypeError, ValueError):
        return fallback


def build_html(analyses: list, asof: str) -> str:
    rows = []
    details = []
    for item in analyses:
        rating = item["rating"] or {}
        previous = item["previous"] or {}
        price = item["price"]
        sentiment = item["sentiment"]
        grade = rating.get("grade", "N/A")
        prev_grade = previous.get("grade", "-")
        rating_date = rating.get("trade_date")
        eligibility_risk = item["eligibility"].get("financial_risk_level", "unknown")
        risk = (eligibility_risk if eligibility_risk == "high"
                else rating.get("financial_risk_level", eligibility_risk))
        risk_flags = item["eligibility"].get("financial_risk_flags") or []
        trend = rating.get("trend_state", "unknown")
        rows.append(
            "<tr>"
            f"<td>{escape(item['group_name'])}</td>"
            f"<td>{escape(item['ts_code'])} {escape(str(item['name']))}</td>"
            f"<td>{_fmt(price['close'])}</td>"
            f"<td>{_fmt(price['pct_chg'], '{:+.2f}%')}</td>"
            f"<td>{escape(str(grade))}（前次 {escape(str(prev_grade))}）"
            f"<br>{escape(str(rating_date or '-'))}</td>"
            f"<td>{escape(str(trend))}</td>"
            f"<td>{escape(str(risk))}{'<br>' + escape(','.join(risk_flags)) if risk_flags else ''}</td>"
            f"<td>{_fmt(sentiment.get('news_score'), '{:+.2f}')}</td>"
            "</tr>")

        messages = item["news"] + item["disclosures"] + item["research"]
        message_html = "".join(f"<li>{escape(title)}</li>" for title in messages)
        if not message_html:
            message_html = "<li>今日未获取到有效新闻、公告或研报</li>"
        details.append(
            f"<h3>{escape(item['ts_code'])} {escape(str(item['name']))}</h3>"
            f"<p>策略：{escape(item['strategy'])}；备注：{escape(item['note']) or '-'}</p>"
            f"<ul>{message_html}</ul>")

    return f"""
    <html><body style="font-family:Arial,'Microsoft YaHei',sans-serif;color:#222">
      <h2>自选股每日汇总 · {escape(asof)}</h2>
      <table border="1" cellspacing="0" cellpadding="6" style="border-collapse:collapse;width:100%">
        <thead><tr><th>分组</th><th>股票</th><th>收盘</th><th>涨跌</th>
        <th>评级</th><th>趋势</th><th>财务风险</th><th>消息分</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
      <h2>今日信息</h2>
      {''.join(details)}
    </body></html>
    """
