"""因子计算

输入：长表 panel（行=股票×交易日），列含
    ts_code, trade_date, close, pct_chg, vol, amount,
    turnover_rate, pe_ttm, pb, ps_ttm, total_mv, circ_mv

输出：截面因子 DataFrame，index=ts_code，列=各因子原始值
"""
import numpy as np
import pandas as pd

from pattern_recognizer import compute_pattern_score


# 因子权重（正向：越大越好；权重为正表示该因子值越大越倾向于选入）
FACTOR_WEIGHTS = {
    "ep_ttm":        0.5,   # 1/PE_TTM，价值（权重降低，避免单一估值霸榜）
    "bp":            1.0,   # 1/PB，价值
    "mom_30":        0.8,   # 过去 30 日动量（约 1.5 个月）
    "reversal_5":    0.5,   # 短期反转（5日收益取负）
    "small_size":    0.6,   # 小市值溢价（流通市值取负）
    "low_vol":       0.6,   # 低波动（20日波动率取负）
    "liquidity":     0.3,   # 20日均换手率（避免选到流动性差的）
    "main_inflow":   1.0,   # 主力资金近 N 日净流入额（捡筹码信号）
    "inflow_ratio": 0.8,   # 净流入占资金总进出比 (in-out)/(in+out)
    "macd_hist":     0.6,   # MACD 柱状值 (DIF-DEA)，正=金叉态
    "macd_slope":    0.4,   # MACD 柱状值近 5 日变化（避开"快死叉"）
    "lxsz":          0.5,   # 量价齐升连涨天数（同花顺榜单，未上榜=0）
    "pattern_score": 0.5,   # 日K形态分（锤子/吞没/黄昏之星等，含量能+位置过滤）
}


def _safe_inv(s) -> pd.Series:
    """安全取倒数：PE/PB ≤ 0 或缺失时置 NaN"""
    if s is None:
        return pd.Series(dtype=float)
    s = pd.to_numeric(s, errors="coerce")
    s = s.where(s > 0, np.nan)
    return 1.0 / s


def compute_all_factors(panel: pd.DataFrame, asof_date: pd.Timestamp) -> pd.DataFrame:
    """从长表 panel 计算截面因子

    panel: 含多日多股票的长表
    asof_date: 截面日期（用最近一个 ≤ asof_date 的交易日的快照）

    返回：index=ts_code 的因子原始值表
    """
    panel = panel.copy()
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    panel = panel.sort_values(["ts_code", "trade_date"])

    asof_date = pd.to_datetime(asof_date)
    panel = panel[panel["trade_date"] <= asof_date]

    last_date = panel["trade_date"].max()
    snap = panel[panel["trade_date"] == last_date].set_index("ts_code")

    # 价值因子（来自 daily_basic）
    ep_ttm = _safe_inv(snap.get("pe_ttm", snap.get("pe")))
    bp = _safe_inv(snap.get("pb"))

    # 规模因子（取负，小市值得分高）
    cm = snap.get("circ_mv") if "circ_mv" in snap.columns else None
    if cm is None:
        small_size = pd.Series(dtype=float)
    else:
        circ_mv = pd.to_numeric(cm, errors="coerce")
        small_size = -np.log(circ_mv.where(circ_mv > 0))

    # 动量/反转：用 close 的对数收益
    # 透视成宽表：行=trade_date, 列=ts_code
    close_wide = panel.pivot_table(index="trade_date", columns="ts_code", values="close")
    log_close = np.log(close_wide.where(close_wide > 0))

    def _ret_n(n: int) -> pd.Series:
        if len(log_close) <= n:
            return pd.Series(dtype=float)
        return (log_close.iloc[-1] - log_close.iloc[-1 - n]).rename(None)

    mom_30 = _ret_n(30)
    reversal_5 = -_ret_n(5)

    # 波动率（20日日收益标准差，取负）
    pct_wide = close_wide.pct_change()
    if len(pct_wide) >= 20:
        vol_20 = pct_wide.iloc[-20:].std()
        low_vol = -vol_20
    else:
        low_vol = pd.Series(dtype=float)

    # MACD 因子（标准参数 12/26/9）
    # DIF = EMA12 - EMA26, DEA = EMA(DIF, 9), HIST = DIF - DEA
    if len(close_wide) >= 26:
        ema12 = close_wide.ewm(span=12, adjust=False).mean()
        ema26 = close_wide.ewm(span=26, adjust=False).mean()
        macd_dif = ema12 - ema26
        macd_dea = macd_dif.ewm(span=9, adjust=False).mean()
        macd_hist_ts = macd_dif - macd_dea
        macd_hist = macd_hist_ts.iloc[-1]
        if len(macd_hist_ts) >= 6:
            macd_slope = macd_hist_ts.iloc[-1] - macd_hist_ts.iloc[-6]
        else:
            macd_slope = pd.Series(dtype=float)
    else:
        macd_hist = pd.Series(dtype=float)
        macd_slope = pd.Series(dtype=float)

    # 流动性（20日平均换手率）
    turn_wide = panel.pivot_table(
        index="trade_date", columns="ts_code", values="turnover_rate"
    )
    if len(turn_wide) >= 20:
        liquidity = turn_wide.iloc[-20:].mean()
    else:
        liquidity = turn_wide.mean()

    # 资金流因子（来自 fund_flow 快照 merge 进 panel，截面值）
    if "fund_net" in snap.columns:
        main_inflow = pd.to_numeric(snap["fund_net"], errors="coerce")
    else:
        main_inflow = pd.Series(dtype=float)

    if "fund_inflow" in snap.columns and "fund_outflow" in snap.columns:
        inflow = pd.to_numeric(snap["fund_inflow"], errors="coerce")
        outflow = pd.to_numeric(snap["fund_outflow"], errors="coerce")
        total = inflow.abs() + outflow.abs()
        inflow_ratio = (inflow - outflow) / total.where(total > 0)
    else:
        inflow_ratio = pd.Series(dtype=float)

    # 量价齐升因子（来自 lxsz_days 字段，未上榜的填 0）
    if "lxsz_days" in snap.columns:
        lxsz = pd.to_numeric(snap["lxsz_days"], errors="coerce").fillna(0)
    else:
        lxsz = pd.Series(dtype=float)

    # 日 K 形态分（对每只股票算 pattern_score）
    pattern_score = panel.groupby("ts_code", sort=False).apply(
        lambda g: compute_pattern_score(g, lookback=5)
    )

    factors = pd.DataFrame({
        "ep_ttm":        ep_ttm,
        "bp":            bp,
        "mom_30":        mom_30,
        "reversal_5":    reversal_5,
        "small_size":    small_size,
        "low_vol":       low_vol,
        "liquidity":     liquidity,
        "main_inflow":   main_inflow,
        "inflow_ratio":  inflow_ratio,
        "macd_hist":     macd_hist,
        "macd_slope":    macd_slope,
        "lxsz":          lxsz,
        "pattern_score": pattern_score,
    })
    factors.index.name = "ts_code"
    return factors
