"""单股量化评级（按经验阈值评分，不依赖全市场参照）

用法:
    python rate.py 002028              # 评级 002028
    python rate.py 600487 --no-flow    # 跳过资金面（不拉全市场快照，更快）

数据：仅拉这一只股票的历史日线 + 可选拉一次全市场资金流快照筛取此股。
"""
import sys
import io
import os
import argparse
from datetime import datetime, timedelta

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import numpy as np
import pandas as pd

from data.fetcher import DataFetcher
from single_grader import grade_single
from news_scorer import compute_news_score
from strategies import get_dim_weights, list_strategies
from pattern_recognizer import compute_pattern_score, list_patterns


def normalize_code(code: str) -> str:
    """6 位数字 -> ts_code 形式"""
    code = code.strip().upper()
    if "." in code:
        return code
    s = code.zfill(6)
    if s.startswith(("6", "9")):
        return f"{s}.SH"
    return f"{s}.SZ"


def compute_tech_factors(daily: pd.DataFrame) -> dict:
    """从历史日线算量价 4 因子"""
    if daily.empty or len(daily) < 30:
        return {}
    df = daily.sort_values("trade_date").reset_index(drop=True)
    close = df["close"].astype(float)
    out = {}

    # 30 日累计收益
    if len(close) > 30:
        out["mom_30"] = float(close.iloc[-1] / close.iloc[-31] - 1)
    # 5 日累计收益
    if len(close) > 5:
        out["reversal_5"] = float(close.iloc[-1] / close.iloc[-6] - 1)
    # 20 日日收益的年化波动率 = std × sqrt(252)
    if len(close) >= 21:
        ret = close.pct_change().iloc[-20:]
        out["low_vol"] = float(ret.std() * np.sqrt(252))
    # 20 日均换手率
    if "turnover_rate" in df.columns and len(df) >= 20:
        out["liquidity"] = float(df["turnover_rate"].iloc[-20:].mean())
    return out


def main():
    parser = argparse.ArgumentParser(description="A股单股量化评级")
    parser.add_argument("code", help="股票代码，如 002028 或 002028.SZ")
    parser.add_argument("--no-flow", action="store_true",
                        help="跳过资金面（不拉全市场资金流快照，速度更快）")
    parser.add_argument("--no-news", action="store_true",
                        help="跳过消息面（不拉新闻/公告/研报，速度更快）")
    parser.add_argument("--lookback", type=int, default=90,
                        help="历史回看天数（默认 90 自然日，约 60 交易日）")
    parser.add_argument("--strategy", default="swing", choices=list_strategies(),
                        help="交易策略 profile: short_term/swing/trend (默认 swing)")
    args = parser.parse_args()

    ts_code = normalize_code(args.code)
    print(f"开始评级: {ts_code}")

    fetcher = DataFetcher()
    asof_dt = datetime.now()
    asof = asof_dt.strftime("%Y%m%d")
    start = (asof_dt - timedelta(days=args.lookback + 30)).strftime("%Y%m%d")

    # ---- 1) 历史日线（量价因子来源）----
    print(f"  [1] 拉取历史日线 {start} ~ {asof} ...")
    daily = fetcher.get_daily(ts_code, start, asof)
    if daily.empty:
        print(f"  错误: {ts_code} 历史数据为空（可能代码错误或已停牌）")
        sys.exit(1)
    print(f"      {len(daily)} 行日线")

    factor_values = compute_tech_factors(daily)

    # 日K形态分（基于同一份日线）
    pattern = compute_pattern_score(daily, lookback=5)
    factor_values["pattern_score"] = pattern
    hits = list_patterns(daily, lookback=5)
    if hits:
        print(f"      形态命中: " + ", ".join(
            f"{d} {n}({dr},{v})" for d, n, dr, v in hits
        ))
    else:
        print(f"      最近 5 日无明显形态信号")

    # ---- 2a) 估值（PE/PB）：单股接口 ak.stock_a_indicator_lg ----
    print(f"  [2a] 拉取估值指标（PE/PB）...")
    ind = fetcher.get_stock_indicator(ts_code)
    if not ind.empty:
        last = ind.iloc[-1]
        for src_key, dst_key in [("pe_ttm", "pe_ttm"), ("pe", "pe_ttm"), ("pb", "pb")]:
            if src_key in last and pd.notna(last[src_key]) and dst_key not in factor_values:
                factor_values[dst_key] = float(last[src_key])
        print(f"      估值: pe_ttm={factor_values.get('pe_ttm', 'N/A')}, "
              f"pb={factor_values.get('pb', 'N/A')}")

    # ---- 2c) 财务质量（ROE / 毛利率）：同花顺 ----
    # 同花顺财报字段是单期值（Q1 / 半年 / Q3 / 年报），ROE 要按季度年化
    print(f"  [2c] 拉取财务摘要（ROE/毛利率）...")
    fin = fetcher.get_stock_financial_abstract(ts_code)
    if not fin.empty:
        last = fin.iloc[-1]
        rpt = last.get("报告期")
        # 季度年化系数：3月底→×4, 6月底→×2, 9月底→×4/3, 12月底→×1
        # 兼容 datetime 和 字符串("YYYY-MM-DD") 两种缓存形态
        month = None
        if hasattr(rpt, "month"):
            month = rpt.month
        elif isinstance(rpt, str) and len(rpt) >= 7 and rpt[4] == "-":
            try:
                month = int(rpt[5:7])
            except ValueError:
                pass
        annualize = {3: 4.0, 6: 2.0, 9: 4.0 / 3, 12: 1.0}.get(month, 1.0)
        if "净资产收益率" in last and pd.notna(last["净资产收益率"]):
            factor_values["roe"] = float(last["净资产收益率"]) * annualize
        # 毛利率本身是比例，不需要年化
        if "销售毛利率" in last and pd.notna(last["销售毛利率"]):
            factor_values["gross_margin"] = float(last["销售毛利率"])
        rpt_s = rpt.strftime("%Y-%m-%d") if hasattr(rpt, "strftime") else str(rpt)
        roe_v = factor_values.get("roe", "N/A")
        gm_v = factor_values.get("gross_margin", "N/A")
        roe_disp = f"{roe_v:.1f}% (年化×{annualize:g})" if isinstance(roe_v, float) else roe_v
        print(f"      最新报告期 {rpt_s}: ROE={roe_disp}, 毛利率={gm_v}%")

    # ---- 2d) 量价齐升（同花顺排行，全市场一次拉，找当前股是否上榜） ----
    print(f"  [2d] 拉取量价齐升排行...")
    lxsz_df = fetcher.get_stock_rank_lxsz()
    if not lxsz_df.empty:
        row = lxsz_df[lxsz_df["ts_code"] == ts_code]
        if not row.empty and "lxsz_days" in row.columns:
            factor_values["lxsz"] = float(row.iloc[0]["lxsz_days"])
            print(f"      上榜：连涨 {int(factor_values['lxsz'])} 天")
        else:
            factor_values["lxsz"] = 0  # 未上榜 = 0
            print(f"      未上榜（中性）")

    # ---- 2b) 股票名（从 spot 快照取，失败不影响）----
    name = ""
    print(f"  [2b] 拉取股票名称...")
    try:
        spot = fetcher.get_market_snapshot()
        row = spot[spot["ts_code"] == ts_code]
        if not row.empty:
            name = row.iloc[0].get("name", "") or ""
    except Exception as e:
        print(f"      [warn] spot 拉取失败（不影响评级）: {type(e).__name__}: {str(e)[:80]}")

    # ---- 3) 资金流 ----
    if not args.no_flow:
        print(f"  [3] 拉取资金流快照（同花顺源）...")
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
                else:
                    print(f"      [warn] {ts_code} 不在资金流快照中")
        except Exception as e:
            print(f"      [warn] 资金流拉取失败: {type(e).__name__}: {str(e)[:80]}")
    else:
        print(f"  [3] 跳过资金面 (--no-flow)")

    # ---- 4) 消息面（新闻 + 公告 + 研报） ----
    if not args.no_news:
        print(f"  [4] 拉取消息面（新闻+公告+研报）...")
        news_df = fetcher.get_stock_news(ts_code)
        disc_df = fetcher.get_stock_disclosure(ts_code, days=30)
        rsr_df = fetcher.get_stock_research(ts_code)
        ns = compute_news_score(news_df, disc_df, rsr_df)
        print(f"      命中: 新闻 {ns['news_hits']} / 公告 {ns['disc_hits']} / 研报 {ns['research_hits']}")
        if not pd.isna(ns["news_score"]):
            factor_values["news_score"] = ns["news_score"]
            print(f"      综合分: {ns['news_score']:+.2f} "
                  f"(新闻{ns['news_part']:+.2f}/公告{ns['disc_part']:+.2f}/研报{ns['research_part']:+.2f})"
                  .replace("nan", "无"))
    else:
        print(f"  [4] 跳过消息面 (--no-news)")

    # ---- 5) 评级 ----
    dim_weights = get_dim_weights(args.strategy)
    rating = grade_single(ts_code=ts_code, name=name, asof=asof,
                          factor_values=factor_values,
                          dim_weights=dim_weights,
                          strategy=args.strategy)

    print()
    print(rating.to_report())

    # 保存评级结果
    os.makedirs("output", exist_ok=True)
    out_path = os.path.join("output",
                            f"rating_{ts_code.replace('.', '_')}_{args.strategy}_{asof}.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(rating.to_report())
    print(f"\n评级报告已保存: {out_path}")


if __name__ == "__main__":
    main()
