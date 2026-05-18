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
        "ep_ttm":       0.1,
        "bp":           0.2,
        "small_size":   0.3,
        "mom_30":       0.6,
        "reversal_5":   0.8,
        "low_vol":      0.0,
        "liquidity":    0.8,
        "main_inflow":  1.5,
        "inflow_ratio": 1.0,
        "macd_hist":    1.0,
        "macd_slope":   0.8,
        "lxsz":         1.0,
    },
    "swing": {
        "ep_ttm":       0.5,
        "bp":           1.0,
        "small_size":   0.6,
        "mom_30":       0.8,
        "reversal_5":   0.5,
        "low_vol":      0.6,
        "liquidity":    0.3,
        "main_inflow":  1.0,
        "inflow_ratio": 0.8,
        "macd_hist":    0.6,
        "macd_slope":   0.4,
        "lxsz":         0.5,
    },
    "trend": {
        "ep_ttm":       0.2,
        "bp":           0.3,
        "small_size":   0.5,
        "mom_30":       1.5,
        "reversal_5":  -0.3,  # 负权重：追涨，不抄底
        "low_vol":      0.0,
        "liquidity":    0.5,
        "main_inflow":  1.0,
        "inflow_ratio": 0.6,
        "macd_hist":    1.0,
        "macd_slope":   0.6,
        "lxsz":         0.8,
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
}


def get_factor_weights(strategy: str) -> dict:
    """获取选股因子权重；未知策略名 fallback 到 swing"""
    return dict(FACTOR_PROFILES.get(strategy, FACTOR_PROFILES["swing"]))


def get_dim_weights(strategy: str) -> dict:
    """获取评级维度权重；未知策略名 fallback 到 swing"""
    return dict(DIM_PROFILES.get(strategy, DIM_PROFILES["swing"]))


def list_strategies() -> list:
    return list(FACTOR_PROFILES.keys())
