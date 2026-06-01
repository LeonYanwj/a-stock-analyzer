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
        "roe":           0.2,   # 短线不太看基本面
        "gross_margin":  0.1,
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
        "roe":           0.8,   # 波段平衡看基本面
        "gross_margin":  0.5,
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
        "roe":           1.0,   # 趋势 + 基本面 = 长牛股特征
        "gross_margin":  0.6,
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


# -------------------------- 调仓模式 --------------------------
# signal   = 信号驱动·增量换仓：每个交易日重算打分，持仓跌出宽限带就换，
#            强势保留，空仓补新晋强势股。适合短线/波段。
# periodic = 周期驱动：到调仓周期才整体清仓重选。适合趋势/长线（少折腾、低换手）。
REBAL_MODE = {
    "short_term":   "signal",    # 短线 1-3 天：必须靠信号及时换
    "swing":        "signal",    # 波段 1-4 周：也用信号，宽限带更宽（见 keep_buffer）
    "trend":        "periodic",  # 趋势 1-3 月：周期调仓即可，避免追涨杀跌
    "ic_optimized": "periodic",
}

# 各策略信号驱动时的「保留宽限带」倍数：持仓股排名 ≤ top_n*buffer 就继续持有。
# 倍数越大越"惰性"（换手越低）。短线小、波段大。
KEEP_BUFFER = {
    "short_term": 1.3,
    "swing":      1.8,
}


def get_rebal_mode(strategy: str) -> str:
    """返回调仓模式：'signal' 或 'periodic'；未知策略默认 periodic（保守）"""
    return REBAL_MODE.get(strategy, "periodic")


def get_keep_buffer(strategy: str) -> float:
    """信号驱动时的保留宽限带倍数；默认 1.5"""
    return KEEP_BUFFER.get(strategy, 1.5)


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
import math

# 单股仓位约束（A 股实战经验）
MIN_PER_POSITION = 5_000      # 单股最少 5000 元（让最低 5 元佣金占比 ≤ 0.1%）
MAX_PER_POSITION = 100_000    # 单股最多 10 万元（超过后冲击成本上升 + 散户难管理）
HARD_MIN_N = 3                # 持仓数硬下限（< 3 只单股黑天鹅风险太大）
HARD_MAX_N = 60               # 持仓数硬上限（> 60 只接近指数化，alpha 被稀释）


def position_range(capital: float) -> tuple:
    """根据资金量返回合理持仓数区间 (min_n, max_n)

    设计原则：
    - 单股仓位 ≥ 5000 元：避免最低 5 元佣金占比飙升
    - 单股仓位 ≤ 10 万元：避免冲击成本 + 利于散户跟踪
    - 全局 3-60 只硬上下限

    Returns:
        (min_n, max_n) 持仓数允许的区间
    """
    max_n_by_cost = int(capital / MIN_PER_POSITION)         # 单股 ≥ 5000 反推的最大持仓
    min_n_by_impact = int(capital / MAX_PER_POSITION)       # 单股 ≤ 10万 反推的最小持仓
    max_n = max(HARD_MIN_N, min(HARD_MAX_N, max_n_by_cost))
    min_n = max(HARD_MIN_N, min(max_n, min_n_by_impact))
    return (min_n, max_n)


def calc_optimal_top_n(capital: float) -> int:
    """在合理区间内的几何中位作为推荐值（不死板，可被 --top 覆盖）

    几何中位 sqrt(min*max) 比算术中位更"中庸"——
    资金量大时不会被 max=60 拉得太高。
    """
    min_n, max_n = position_range(capital)
    return max(min_n, min(max_n, round(math.sqrt(min_n * max_n))))


def validate_top_n(top_n: int, capital: float) -> str:
    """验证 top_n 是否在合理区间，返回警告文本（OK 时返回空字符串）"""
    if capital <= 0:
        return ""
    min_n, max_n = position_range(capital)
    if top_n < min_n:
        per_pos = capital / top_n
        return (f"[警告] top={top_n} 低于推荐区间 [{min_n}, {max_n}]：单股 {per_pos:,.0f} 元"
                f"，超过 10万 冲击成本会上升")
    if top_n > max_n:
        per_pos = capital / top_n
        return (f"[警告] top={top_n} 超过推荐区间 [{min_n}, {max_n}]：单股 {per_pos:,.0f} 元"
                f"，低于 5000 佣金占比会很高")
    return ""


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
    print("资金 -> 持仓数（区间 + 推荐值）")
    print("=" * 70)
    print(f"{'资金':<12} {'区间':<12} {'推荐':<6} {'单股仓位':<14}")
    print("-" * 70)
    for c in [20000, 40000, 80000, 200000, 500000, 1500000, 5000000, 20000000, 50000000]:
        mn, mx = position_range(c)
        rec = calc_optimal_top_n(c)
        per_pos = c / rec
        print(f"{c:>10,}元  [{mn},{mx}]      {rec:<6}  {per_pos:>10,.0f} 元/只")
