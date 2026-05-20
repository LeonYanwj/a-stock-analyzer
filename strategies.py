"""交易策略 Profile

每个策略对应两套权重：
- FACTOR_PROFILES: 选股 (screen.py) 用的因子权重（横截面 z-score 加权）
- DIM_PROFILES:    评级 (rate.py) 用的维度权重

策略说明：
- short_term  (1-3 天)：重资金流 / 量价齐升 / MACD / 反转。不在意基本面，要追近期热点。
- swing       (1-4 周)：当前默认，平衡型，5 维度都用。
- trend       (1-3 月)：重长动量 / MACD 持续金叉态势 / 资金持续流入，反转因子翻转
                       （想追涨而非抄底）。

选股权重支持负值——负权重相当于"取反后参与打分"，
例如趋势策略里 reversal_5 = -0.3 表示"近 5 日上涨得分高"（追涨）。
"""


# -------------------------- 选股因子权重 --------------------------
FACTOR_PROFILES = {
    "short_term": {
        "ep_ttm":        0.1,
        "bp":            0.2,
        "small_size":    0.3,
        "mom_30":        0.6,
        "reversal_5":    0.8,
        "low_vol":       0.0,
        "liquidity":     0.8,
        "main_inflow":   1.5,
        "inflow_ratio":  1.0,
        "macd_hist":     1.0,
        "macd_slope":    0.8,
        "lxsz":          1.0,
        "pattern_score": 0.8,   # 形态对短线最有用
    },
    "swing": {
        "ep_ttm":        0.5,
        "bp":            1.0,
        "small_size":    0.6,
        "mom_30":        0.8,
        "reversal_5":    0.5,
        "low_vol":       0.6,
        "liquidity":     0.3,
        "main_inflow":   1.0,
        "inflow_ratio":  0.8,
        "macd_hist":     0.6,
        "macd_slope":    0.4,
        "lxsz":          0.5,
        "pattern_score": 0.5,
    },
    "trend": {
        "ep_ttm":        0.2,
        "bp":            0.3,
        "small_size":    0.5,
        "mom_30":        1.5,
        "reversal_5":   -0.3,  # 负权重：追涨，不抄底
        "low_vol":       0.0,
        "liquidity":     0.5,
        "main_inflow":   1.0,
        "inflow_ratio":  0.6,
        "macd_hist":     1.0,
        "macd_slope":    0.6,
        "lxsz":          0.8,
        "pattern_score": 0.3,   # 趋势策略不太依赖反转形态
    },
    # 基于 2022-2024 多窗口 IC 分析的稳健调权重版本
    # walk-forward 验证：500 只主板下 3 窗口胜率 67%，
    # 平均年化 +17.28% / 超额沪深300 +21.29%
    "ic_optimized": {
        "ep_ttm":        0.5,    # 估值因子保持
        "bp":            1.0,
        "small_size":    0.6,
        # 量价：按多窗口 IC 稳定性重排
        "mom_30":        0.5,    # 0.8 -> 0.5 (IC 跨期不稳定)
        "reversal_5":    0.8,    # 0.5 -> 0.8 (多数窗口正 IC)
        "low_vol":       1.2,    # 0.6 -> 1.2 (稳定最高 IC)
        "liquidity":     0.1,    # 0.3 -> 0.1 (多数窗口负 IC)
        # 资金面保持
        "main_inflow":   1.0,
        "inflow_ratio":  0.8,
        # MACD/形态：多次显著负 IC，大幅降权
        "macd_hist":     0.2,    # 0.6 -> 0.2
        "macd_slope":    0.1,    # 0.4 -> 0.1
        "lxsz":          0.5,    # 保持
        "pattern_score": 0.0,    # 0.5 -> 0.0 (多次反向，关闭)
    },
}


# -------------------------- 评级维度权重 --------------------------
DIM_PROFILES = {
    "short_term": {
        "tech":    1.5,  # 量价 + lxsz 重要
        "value":   0.3,  # 估值不重要
        "quality": 0.3,  # 基本面不重要
        "flow":    1.5,  # 资金最重要
        "news":    1.0,  # 消息面有用
    },
    "swing": {
        "tech":    1.0,
        "value":   1.0,
        "quality": 1.0,
        "flow":    1.2,
        "news":    0.8,
    },
    "trend": {
        "tech":    1.5,  # MACD/动量
        "value":   0.5,
        "quality": 0.5,
        "flow":    1.0,
        "news":    0.6,
    },
    "ic_optimized": {
        "tech":    1.0,
        "value":   1.0,
        "quality": 1.0,
        "flow":    1.2,
        "news":    0.8,
    },
}


def get_factor_weights(strategy: str) -> dict:
    """获取选股因子权重；未知策略名 fallback 到 swing"""
    return dict(FACTOR_PROFILES.get(strategy, FACTOR_PROFILES["swing"]))


def get_dim_weights(strategy: str) -> dict:
    """获取评级维度权重；未知策略名 fallback 到 swing"""
    return dict(DIM_PROFILES.get(strategy, DIM_PROFILES["swing"]))


def list_strategies() -> list:
    return list(FACTOR_PROFILES.keys())


# ----------------------------------------------------------------------
# 根据资金量自动算合理持仓数
# ----------------------------------------------------------------------
def calc_optimal_top_n(capital: float) -> int:
    """根据资金量按经验规则返回持仓数。

    A 股佣金有"最低 5 元/笔"规则：
    - 单笔成交额 ≥ 5000 元，佣金占比 ≤ 0.1%（接近万一标牌价）
    - 单笔成交额 < 5000 元，被强制收 5 元，等于变相提价
    - 例如：单笔 1000 元，佣金 5 元 = 0.5%（双边 1%）

    所以核心约束是：**单股仓位 ≥ 5000 元**。
    （并非"能买 1 手"那么简单，因为 1 手才 1500 元的话佣金占比飙到 0.33%）

    Args:
        capital: 总资金（元）

    Returns:
        建议持仓数（3/5/8/10/15/25/40/60 中的一个）

    设计哲学：
    - 学术研究：8-12 只能消除 80%+ 特质风险，再多收益递减
    - 散户实战：3-10 只重仓博精选更符合个人习惯
    - 持仓 < 5 只确实有单股黑天鹅风险（一只跌停 ~ -3~5% 组合）
    """
    if capital < 30_000:       return 3    # < 3 万：每只 ~10000，重仓博精选
    if capital < 50_000:       return 5    # 3-5 万：每只 ~8000
    if capital < 100_000:      return 8    # 5-10 万：每只 ~10000，5-7 行业
    if capital < 300_000:      return 10   # 10-30 万：每只 ~20000
    if capital < 1_000_000:    return 15   # 30-100 万：每只 ~40000
    if capital < 3_000_000:    return 25   # 100-300 万
    if capital < 10_000_000:   return 40   # 300-1000 万
    return 60                               # > 1000 万（大资金避免单只冲击）


def warn_if_capital_too_small(capital: float) -> str:
    """资金太小时返回警告文本（< 5 万很难跑多因子策略）"""
    if capital < 30_000:
        return ("[警告] 资金 < 3 万：多因子等权策略的手续费消耗很大"
                "（最低 5 元佣金占比 > 0.1%）。建议改买 ETF（如 510300 沪深300）"
                "或减少换仓频率到月度。")
    if capital < 50_000:
        return ("[提示] 资金 < 5 万：持仓数已限制到 5 只。"
                "建议调仓频率改为月度（--rebal-weeks 4）降低手续费。")
    return ""


def describe_capital_to_top_n(capital: float) -> str:
    """返回友好描述：资金 -> 持仓数 -> 单股仓位"""
    n = calc_optimal_top_n(capital)
    per_pos = capital / n
    return (f"资金 {capital:>12,.0f} → 持仓 {n:>3} 只 "
            f"(每只 ~{per_pos:>8,.0f} 元)")


if __name__ == "__main__":
    # 自检：常见档位
    print("资金 -> 持仓数 映射表（考虑最低 5 元佣金陷阱）")
    print("-" * 60)
    for c in [20000, 40000, 80000, 200000, 500000, 1500000, 5000000, 20000000, 50000000]:
        line = describe_capital_to_top_n(c)
        warn = warn_if_capital_too_small(c)
        print(line, "  " + warn if warn else "")
