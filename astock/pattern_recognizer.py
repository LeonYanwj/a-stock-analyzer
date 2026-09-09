"""日K形态识别（精选 4 个核心形态，含位置/量能确认）

设计原则（基于形态分析的研究共识）：
- 单纯 K 线形态胜率仅略高于 50%
- 加上"位置过滤"（下跌后的看涨形态、上涨后的看跌形态）能提升 10%+
- 加上"量能确认"（反转形态必须放量）能再提升 5%+

输出综合 pattern_score（约 -3 ~ +3）作为单个因子，
正值=看涨信号，负值=看跌信号。
"""
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------
# 单形态识别（OHLC 规则，无外部依赖）
# ---------------------------------------------------------------------
def is_hammer(o, c, h, l) -> bool:
    """锤子线：长下影 + 小实体 + 上影线很短（底部反转信号）

    规则：
    - 实体长度 < 全长度 × 0.3
    - 下影线 > 实体 × 2
    - 上影线 < 实体
    """
    if any(pd.isna([o, c, h, l])):
        return False
    body = abs(c - o)
    full = h - l
    if full == 0:
        return False
    upper = h - max(c, o)
    lower = min(c, o) - l
    return (body / full < 0.3
            and lower > body * 2
            and upper < max(body * 0.5, 0.001))


def is_bullish_engulfing(prev_o, prev_c, today_o, today_c) -> bool:
    """看涨吞没：昨阴今阳，今实体完全包住昨实体"""
    if any(pd.isna([prev_o, prev_c, today_o, today_c])):
        return False
    return (prev_c < prev_o
            and today_c > today_o
            and today_o <= prev_c
            and today_c >= prev_o)


def is_bearish_engulfing(prev_o, prev_c, today_o, today_c) -> bool:
    """看跌吞没：昨阳今阴"""
    if any(pd.isna([prev_o, prev_c, today_o, today_c])):
        return False
    return (prev_c > prev_o
            and today_c < today_o
            and today_o >= prev_c
            and today_c <= prev_o)


def is_evening_star(o1, c1, o2, c2, o3, c3) -> bool:
    """黄昏之星（3 根 K 线顶部反转）：阳-小-阴"""
    if any(pd.isna([o1, c1, o2, c2, o3, c3])):
        return False
    body1 = c1 - o1
    body3 = o3 - c3
    body2 = abs(c2 - o2)
    if body1 <= 0 or body3 <= 0:           # 第 1 日须阳、第 3 日须阴
        return False
    if body2 > body1 * 0.5:                # 第 2 日实体须小
        return False
    if c3 > (o1 + c1) / 2:                 # 第 3 日须跌破第 1 日中点
        return False
    return True


# ---------------------------------------------------------------------
# 综合 pattern_score：对单只股票的最近若干天检测形态 + 位置/量能过滤
# ---------------------------------------------------------------------
def compute_pattern_score(daily: pd.DataFrame, lookback: int = 5) -> float:
    """对一只股票的历史日线计算形态分

    Args:
        daily: 含 open/high/low/close/vol 的 DataFrame（按 trade_date 升序）
        lookback: 检测最近多少天内的形态（默认 5 天）

    Returns:
        score: -3 ~ +3 的连续分数。正=看涨，负=看跌。
    """
    if daily is None or daily.empty:
        return 0.0
    required = {"open", "high", "low", "close"}
    if not required.issubset(daily.columns):
        return 0.0  # 缺关键字段，跳过形态识别
    df = daily.sort_values("trade_date").reset_index(drop=True)
    if len(df) < 31:
        return 0.0  # 数据不足

    # 30 日动量：用于"位置"过滤（下跌后的看涨形态才有效）
    mom_30 = float(df["close"].iloc[-1] / df["close"].iloc[-31] - 1)

    # 5 日均量：用于"量能确认"
    if "vol" in df.columns:
        vol_avg_5 = float(df["vol"].iloc[-5:].mean())
    else:
        vol_avg_5 = 0.0

    score = 0.0
    n = len(df)
    start = max(2, n - lookback)  # 至少从 index 2 开始（黄昏之星要 3 根）

    for i in range(start, n):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        prev2 = df.iloc[i - 2]

        # 时间衰减：当日 1.0, 前 1 日 0.7, 前 2 日 0.49 ...
        days_ago = n - 1 - i
        decay = 0.7 ** days_ago

        # 量能放大判断
        vol_amplify = bool(row.get("vol", 0) > vol_avg_5) if vol_avg_5 > 0 else False
        vol_w = 1.5 if vol_amplify else 0.6   # 放量加权，缩量降权

        # ---- 锤子线（看涨）：只在下跌段才算数 ----
        if is_hammer(row["open"], row["close"], row["high"], row["low"]):
            if mom_30 < -0.05:
                score += 1.5 * vol_w * decay

        # ---- 看涨吞没：只在下跌段才算数 ----
        if is_bullish_engulfing(prev["open"], prev["close"],
                                row["open"], row["close"]):
            if mom_30 < -0.05:
                score += 2.0 * vol_w * decay

        # ---- 看跌吞没：只在上涨段才算数 ----
        if is_bearish_engulfing(prev["open"], prev["close"],
                                row["open"], row["close"]):
            if mom_30 > 0.05:
                score -= 2.0 * vol_w * decay

        # ---- 黄昏之星：只在上涨段才算数 ----
        if is_evening_star(prev2["open"], prev2["close"],
                           prev["open"], prev["close"],
                           row["open"], row["close"]):
            if mom_30 > 0.05:
                score -= 2.0 * vol_w * decay

    # 限制范围
    return float(np.clip(score, -3.0, 3.0))


def list_patterns(daily: pd.DataFrame, lookback: int = 5) -> list:
    """列出最近 lookback 天内命中的形态（用于评级报告展示）

    Returns: [(日期, 形态名, 方向, 量能确认)]
    """
    out = []
    if daily is None or daily.empty:
        return out
    if not {"open", "high", "low", "close"}.issubset(daily.columns):
        return out
    df = daily.sort_values("trade_date").reset_index(drop=True)
    if len(df) < 31:
        return out

    mom_30 = float(df["close"].iloc[-1] / df["close"].iloc[-31] - 1)
    vol_avg_5 = float(df["vol"].iloc[-5:].mean()) if "vol" in df.columns else 0.0

    n = len(df)
    start = max(2, n - lookback)
    for i in range(start, n):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        prev2 = df.iloc[i - 2]
        date = row["trade_date"]
        d_str = date.strftime("%m-%d") if hasattr(date, "strftime") else str(date)[:10]
        vol_amp = "放量" if (vol_avg_5 > 0 and row.get("vol", 0) > vol_avg_5) else "缩量"

        if is_hammer(row["open"], row["close"], row["high"], row["low"]):
            if mom_30 < -0.05:
                out.append((d_str, "锤子线", "看涨", vol_amp))
        if is_bullish_engulfing(prev["open"], prev["close"],
                                row["open"], row["close"]):
            if mom_30 < -0.05:
                out.append((d_str, "看涨吞没", "看涨", vol_amp))
        if is_bearish_engulfing(prev["open"], prev["close"],
                                row["open"], row["close"]):
            if mom_30 > 0.05:
                out.append((d_str, "看跌吞没", "看跌", vol_amp))
        if is_evening_star(prev2["open"], prev2["close"],
                           prev["open"], prev["close"],
                           row["open"], row["close"]):
            if mom_30 > 0.05:
                out.append((d_str, "黄昏之星", "看跌", vol_amp))
    return out
