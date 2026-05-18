"""个股多维度评级

输入：compute_all_factors 算出的因子表（行=股票，列=因子）
输出：含 3 个维度独立分数 + 评级 + 综合评级的 DataFrame

设计：
- 3 个维度（value / tech / flow）按独立的因子子集横截面 z-score 加权求和
- 每个维度在全样本上按分位数分档（S/A/B/C/D）
- 综合评级 = 各维度加权后再分档

评级是横截面概念：必须有大样本（建议 ≥ 300 只）才有统计意义；
小样本（如指定几只股票）应在全市场样本上算分位数后再筛输出。
"""
import numpy as np
import pandas as pd

from selector import winsorize, zscore


# 3 个维度的因子组成（与 factors.FACTOR_WEIGHTS 中的因子名对齐）
DIMENSIONS = {
    "value": {
        "label": "价值",
        "factors": {
            "ep_ttm":     1.0,
            "bp":         1.0,
            "small_size": 0.6,
        },
    },
    "tech": {
        "label": "量价",
        "factors": {
            "mom_30":     0.8,
            "reversal_5": 0.5,
            "low_vol":    0.6,
            "liquidity":  0.3,
        },
    },
    "flow": {
        "label": "资金",
        "factors": {
            "main_inflow":  1.0,
            "inflow_ratio": 0.8,
        },
    },
}

# 综合评级时三个维度的权重
TOTAL_DIM_WEIGHTS = {"value": 1.0, "tech": 1.0, "flow": 1.2}

# 分档分位数边界 + 等级标签
# 例如 0.05 表示分数前 5% 评 S，5%-20% 评 A
GRADE_BOUNDS = [0.05, 0.20, 0.50, 0.80]
GRADES = ["S", "A", "B", "C", "D"]


def _dim_score(factors: pd.DataFrame, dim: str) -> pd.Series:
    """维度内：缩尾 + z-score + 加权求和

    返回：index=ts_code 的维度分数（NaN 表示该维度无任何因子可用）
    """
    weights = DIMENSIONS[dim]["factors"]
    cols = [c for c in weights if c in factors.columns]
    if not cols:
        return pd.Series(dtype=float, index=factors.index)

    z = pd.DataFrame(index=factors.index)
    for c in cols:
        z[c] = zscore(winsorize(factors[c]))

    valid = z.notna().sum(axis=1)
    weight_sum = sum(abs(weights[c]) for c in cols)
    raw = (z.fillna(0.0) * pd.Series(weights)[cols]).sum(axis=1) / weight_sum
    # 至少要有一半因子有效才算这一维度
    min_valid = max(1, len(cols) // 2)
    return raw.where(valid >= min_valid)


def _assign_grade(s: pd.Series) -> pd.Series:
    """按分位数分档：返回 S/A/B/C/D（NaN 输入返回 NaN）"""
    out = pd.Series(np.nan, index=s.index, dtype=object)
    valid = s.dropna()
    if valid.empty:
        return out
    # 计算分位数阈值
    thresholds = [valid.quantile(1 - b) for b in GRADE_BOUNDS]
    # thresholds[0] = 95 分位（S 起点），[1]=80（A 起点），[2]=50（B 起点），[3]=20（C 起点）
    for idx, v in valid.items():
        if v >= thresholds[0]:
            out[idx] = GRADES[0]   # S
        elif v >= thresholds[1]:
            out[idx] = GRADES[1]   # A
        elif v >= thresholds[2]:
            out[idx] = GRADES[2]   # B
        elif v >= thresholds[3]:
            out[idx] = GRADES[3]   # C
        else:
            out[idx] = GRADES[4]   # D
    return out


def grade_all(factors: pd.DataFrame) -> pd.DataFrame:
    """对因子表做多维度评级

    Returns:
        DataFrame: index=ts_code, 列含
            score_value/score_tech/score_flow（原始维度分）
            grade_value/grade_tech/grade_flow（维度评级）
            score_total（综合分）
            grade_total（综合评级）
    """
    out = pd.DataFrame(index=factors.index)

    # 各维度独立分数 + 评级
    for dim in DIMENSIONS:
        sc = _dim_score(factors, dim)
        out[f"score_{dim}"] = sc
        out[f"grade_{dim}"] = _assign_grade(sc)

    # 综合分：维度分按权重求和（缺失维度按 0 处理但要记数）
    dim_cols = [f"score_{d}" for d in DIMENSIONS]
    weights = pd.Series(
        {f"score_{d}": w for d, w in TOTAL_DIM_WEIGHTS.items()}
    )
    valid_dims = out[dim_cols].notna().sum(axis=1)
    weighted = (out[dim_cols].fillna(0.0) * weights[dim_cols]).sum(axis=1)
    total_w = weights[dim_cols].abs().sum()
    out["score_total"] = (weighted / total_w).where(valid_dims >= 2)
    out["grade_total"] = _assign_grade(out["score_total"])

    return out
