"""消息面评分（关键词匹配 + 研报评级映射 + 时间衰减）

数据源：3 类
- 个股新闻（stock_news_em）：用 TITLE_KEYWORDS 匹配标题
- 巨潮公告（stock_zh_a_disclosure_report_cninfo）：同上
- 东财研报（stock_research_report_em）：直接读"东财评级"字段映射分数

综合分 = 新闻分 * 0.4 + 公告分 * 0.3 + 研报分 * 0.3
"""
from datetime import datetime
import numpy as np
import pandas as pd


# 关键词 -> 情感权重（出现在新闻/公告标题中即触发）
TITLE_KEYWORDS = {
    # 强正面 +2
    "业绩预增":     2.0,
    "业绩预喜":     2.0,
    "净利润大增":   2.0,
    "净利润增长":   1.5,
    "中标":         2.0,
    "重大合同":     2.0,
    "签订重大":     1.5,
    "战略合作":     1.5,
    "获批":         1.0,
    "回购":         1.5,
    "增持":         1.5,
    "实控人增持":   2.0,
    "股东增持":     1.5,
    "定增完成":     1.5,
    "解禁完成":     1.0,
    "扭亏":         2.0,
    "扭亏为盈":     2.5,
    "新高":         1.0,
    "上调评级":     1.0,
    # 中性偏正 +0.5
    "签订":         0.5,
    "中标候选人":   1.0,

    # 负面 -1
    "减持":            -1.5,
    "拟减持":          -1.5,
    "股东减持":        -1.5,
    "高管减持":        -1.5,
    "大股东减持":      -2.0,
    "下调评级":        -1.0,
    "下调":            -0.5,
    "诉讼":            -1.0,
    "处罚":            -1.5,
    "立案":            -2.5,
    "立案调查":        -3.0,
    "ST":              -3.0,
    "*ST":             -3.0,
    "退市":            -3.0,

    # 强负面 -2
    "业绩预减":     -2.0,
    "业绩预亏":     -2.5,
    "业绩大幅下滑": -2.0,
    "亏损":         -1.0,
    "违规":         -2.0,
    "违法":         -2.0,
    "财务造假":     -3.0,
    "造假":         -2.5,
    "停牌":         -1.5,
    "重大风险":     -2.0,
    "商誉减值":     -1.5,
    "资产减值":     -1.0,
}


# 研报"东财评级"文字 -> 分数
RESEARCH_RATING_SCORES = {
    "强烈买入": 2.5,
    "买入":     2.0,
    "推荐":     2.0,
    "强烈推荐": 2.5,
    "增持":     1.0,
    "审慎增持": 0.8,
    "谨慎增持": 0.8,
    "看好":     1.0,
    "中性":     0.0,
    "持有":     0.0,
    "观望":     0.0,
    "审慎":     -0.3,
    "减持":     -1.0,
    "卖出":     -2.0,
    "回避":     -1.5,
}


def _decay_weight(days_ago: float, halflife: float = 14.0) -> float:
    """时间衰减权重：越近权重越高（半衰期 14 天）"""
    if days_ago < 0:
        return 1.0
    return 0.5 ** (days_ago / halflife)


def _match_title_score(title: str) -> float:
    """对单条标题做关键词匹配，返回情感分（可正可负）"""
    if not title:
        return 0.0
    score = 0.0
    for kw, w in TITLE_KEYWORDS.items():
        if kw in title:
            score += w
    return score


def score_titles(df: pd.DataFrame, title_col: str, date_col: str,
                 now: datetime = None, halflife: float = 14.0) -> tuple:
    """对一批 (标题, 日期) 数据计算综合情感分

    Returns:
        (综合分, 命中条数)，无数据返回 (0.0, 0)
    """
    if df is None or df.empty:
        return 0.0, 0
    now = now or datetime.now()
    total_w_score = 0.0
    total_w = 0.0
    hits = 0
    for _, row in df.iterrows():
        title = str(row.get(title_col, "") or "")
        sc = _match_title_score(title)
        if sc == 0.0:
            continue
        try:
            d = pd.to_datetime(row.get(date_col))
            days_ago = max(0, (now - d).days)
        except Exception:
            days_ago = 0
        w = _decay_weight(days_ago, halflife)
        total_w_score += sc * w
        total_w += w
        hits += 1
    if total_w == 0:
        return 0.0, 0
    return total_w_score / total_w, hits


def score_research(df: pd.DataFrame, rating_col: str = "东财评级",
                   date_col: str = "日期",
                   now: datetime = None, halflife: float = 30.0) -> tuple:
    """对研报评级表打分（评级文字 -> 分数 + 时间衰减）

    研报半衰期更长（30 天），因为研报频率比新闻低
    """
    if df is None or df.empty:
        return 0.0, 0
    now = now or datetime.now()
    total_w_score = 0.0
    total_w = 0.0
    hits = 0
    for _, row in df.iterrows():
        rating = str(row.get(rating_col, "") or "").strip()
        sc = RESEARCH_RATING_SCORES.get(rating)
        if sc is None:
            continue
        try:
            d = pd.to_datetime(row.get(date_col))
            days_ago = max(0, (now - d).days)
        except Exception:
            days_ago = 0
        w = _decay_weight(days_ago, halflife)
        total_w_score += sc * w
        total_w += w
        hits += 1
    if total_w == 0:
        return 0.0, 0
    return total_w_score / total_w, hits


def compute_news_score(news_df, disclosure_df, research_df) -> dict:
    """综合三种数据源算 news_score

    Returns:
        {
            "news_score":   综合分（约 -3 ~ +3）
            "news_part":    新闻分,
            "disc_part":    公告分,
            "research_part":研报分,
            "news_hits":    命中数,
            "disc_hits":    命中数,
            "research_hits":命中数,
        }
    """
    news_s, news_hits = score_titles(news_df, "新闻标题", "发布时间", halflife=10)
    disc_s, disc_hits = score_titles(disclosure_df, "公告标题", "公告时间", halflife=14)
    rsr_s, rsr_hits = score_research(research_df, "东财评级", "日期", halflife=30)

    parts, weights = [], []
    if news_hits > 0:
        parts.append(news_s);     weights.append(0.4)
    if disc_hits > 0:
        parts.append(disc_s);     weights.append(0.3)
    if rsr_hits > 0:
        parts.append(rsr_s);      weights.append(0.3)

    if not parts:
        total = np.nan
    else:
        w = np.array(weights)
        total = float(np.dot(parts, w) / w.sum())

    return {
        "news_score":     total,
        "news_part":      news_s if news_hits > 0 else np.nan,
        "disc_part":      disc_s if disc_hits > 0 else np.nan,
        "research_part":  rsr_s if rsr_hits > 0 else np.nan,
        "news_hits":      news_hits,
        "disc_hits":      disc_hits,
        "research_hits":  rsr_hits,
    }
