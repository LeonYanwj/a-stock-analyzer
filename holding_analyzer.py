"""实盘持仓盘后全方位分析 + 邮件报告

对每只实盘持仓股：
  - 复用单股 5 维评级（api.routes.rate._do_rate：技术/价值/质量/资金/消息）
  - 拉当天新闻 / 公告 / 研报原文
  - 当日价格 / 涨跌 / 浮盈
汇总成 HTML 报告，经 notify.send_mail 推送。

盘后由 APScheduler 自动调用 run_and_notify()，也可经 SSE 接口手动触发。
"""
from datetime import date
import pandas as pd

import real_holding
import notify
from data.fetcher import DataFetcher


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        return pd.DataFrame()


def _top(df, cols, n):
    if df is None or df.empty:
        return []
    cols = [c for c in cols if c in df.columns]
    if not cols:
        return []
    return df[cols].head(n).astype(str).to_dict("records")


def analyze_one(h: dict, strategy: str = "swing", fetcher=None) -> dict:
    """分析单只持仓。h 含 ts_code/name/qty/cost/buy_date。"""
    fetcher = fetcher or DataFetcher()
    ts_code = h["ts_code"]
    out = {
        "ts_code": ts_code, "name": h.get("name") or "",
        "qty": int(h["qty"]), "cost": float(h["cost"]),
        "buy_date": str(h.get("buy_date") or ""),
    }

    # 1. 5 维评级（复用 rate 路由核心）
    try:
        from api.routes.rate import _do_rate, _rating_to_dict
        out["rating"] = _rating_to_dict(_do_rate(ts_code, strategy, False, False))
    except Exception as e:
        out["rating"] = None
        out["rating_error"] = f"{type(e).__name__}: {e}"

    # 2. 当日价格 / 涨跌 / 浮盈
    try:
        spot = fetcher.get_market_snapshot()
        row = spot[spot["ts_code"] == ts_code]
        if not row.empty:
            r = row.iloc[0]
            price = float(r["close"]) if pd.notna(r.get("close")) else None
            out["price"] = price
            out["pct_chg"] = float(r["pct_chg"]) if pd.notna(r.get("pct_chg")) else None
            if price and out["cost"]:
                out["float_pct"] = price / out["cost"] - 1
    except Exception:
        pass

    # 3. 当天信息原文
    out["news"] = _top(_safe(fetcher.get_stock_news, ts_code),
                       ["新闻标题", "发布时间", "文章来源"], 5)
    out["disclosure"] = _top(_safe(fetcher.get_stock_disclosure, ts_code, days=7),
                             ["公告标题", "公告时间"], 5)
    out["research"] = _top(_safe(fetcher.get_stock_research, ts_code),
                           ["报告名称", "机构", "东财评级", "日期"], 3)
    return out


# ---------------------- 报告 ----------------------
def _fmt_pct(v):
    return f"{v*100:+.2f}%" if isinstance(v, (int, float)) else "—"


def _rating_line(rating: dict) -> str:
    if not rating:
        return '<span style="color:#999">评级不可用</span>'
    dims = " · ".join(f'{d["label"]}{d["stars"]}★' for d in rating.get("dimensions", []))
    return (f'综合 <b>{rating.get("grade","")}</b> '
            f'{rating.get("overall_stars","")}★ <span style="color:#666">({dims})</span>')


def _list_html(items, fields):
    if not items:
        return '<span style="color:#999">无</span>'
    lis = []
    for it in items:
        parts = [str(it.get(f, "")) for f in fields if it.get(f)]
        lis.append("<li>" + " — ".join(parts) + "</li>")
    return "<ul style='margin:4px 0;padding-left:18px'>" + "".join(lis) + "</ul>"


def build_report_html(analyses: list, asof: str) -> str:
    blocks = []
    for a in analyses:
        price = a.get("price")
        head = (f'{a["ts_code"]} {a["name"]} ｜ 今日 '
                f'{price if price is not None else "—"} ({_fmt_pct(a.get("pct_chg"))})')
        pos = (f'持仓 {a["qty"]}股 成本 {a["cost"]} 浮盈 '
               f'<b style="color:{"#c0392b" if (a.get("float_pct") or 0)>=0 else "#27ae60"}">'
               f'{_fmt_pct(a.get("float_pct"))}</b>')
        blocks.append(f"""
<div style="border:1px solid #eee;border-radius:8px;padding:12px;margin:10px 0">
  <div style="font-size:16px;font-weight:bold;color:#2c3e50">{head}</div>
  <div style="margin:4px 0;color:#444">{pos}</div>
  <div style="margin:6px 0">{_rating_line(a.get("rating"))}</div>
  <div style="margin-top:8px">📰 <b>新闻</b>{_list_html(a.get("news"), ["新闻标题","文章来源","发布时间"])}</div>
  <div>📢 <b>公告</b>{_list_html(a.get("disclosure"), ["公告标题","公告时间"])}</div>
  <div>📈 <b>研报</b>{_list_html(a.get("research"), ["机构","东财评级","报告名称","日期"])}</div>
</div>""")
    return f"""<div style="font-family:-apple-system,Segoe UI,Microsoft YaHei,sans-serif;max-width:680px">
<h2 style="color:#2c3e50">📊 持仓盘后分析 · {asof}（{len(analyses)} 只）</h2>
{''.join(blocks)}
<p style="color:#999;font-size:12px">本邮件由 A 股量化系统自动生成，仅供参考，不构成投资建议。</p>
</div>"""


def run_and_notify(strategy: str = "swing", send: bool = True) -> dict:
    """分析所有实盘持仓并发邮件。供调度/SSE 调用。

    Returns: {ok, count, mail?, analyses, reason?}
    """
    holdings = real_holding.list_holdings()
    if holdings.empty:
        return {"ok": False, "reason": "NO_HOLDINGS", "count": 0, "analyses": []}

    fetcher = DataFetcher()
    analyses = [analyze_one(h, strategy, fetcher) for h in holdings.to_dict("records")]
    asof = date.today().isoformat()
    html = build_report_html(analyses, asof)
    result = {"ok": True, "count": len(analyses), "asof": asof, "analyses": analyses}
    if send:
        subject = f"持仓盘后分析 · {asof}（{len(analyses)}只）"
        result["mail"] = notify.send_mail(subject, html)
    return result
