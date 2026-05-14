"""选股器：因子标准化 → 加权打分 → 排序"""
import numpy as np
import pandas as pd

from factors import FACTOR_WEIGHTS


def winsorize(s: pd.Series, lower=0.01, upper=0.99) -> pd.Series:
    """分位数缩尾，抑制极端值"""
    s = pd.to_numeric(s, errors="coerce")
    lo, hi = s.quantile(lower), s.quantile(upper)
    return s.clip(lower=lo, upper=hi)


def zscore(s: pd.Series) -> pd.Series:
    """横截面 z-score 标准化"""
    s = pd.to_numeric(s, errors="coerce")
    mu, sd = s.mean(), s.std()
    if not sd or np.isnan(sd):
        return pd.Series(0.0, index=s.index)
    return (s - mu) / sd


def score(factors: pd.DataFrame,
          weights: dict = None,
          min_valid_factors: int = 4) -> pd.DataFrame:
    """对因子表打分

    Args:
        factors: index=ts_code 的因子原始值表
        weights: 因子权重 dict（取 FACTOR_WEIGHTS）
        min_valid_factors: 至少有效因子数（NaN 太多的股票剔除）

    返回：含 score 列的 DataFrame，按 score 降序
    """
    weights = weights or FACTOR_WEIGHTS
    cols = [c for c in weights if c in factors.columns]

    # 缩尾 + z-score
    z = pd.DataFrame(index=factors.index)
    for c in cols:
        z[c] = zscore(winsorize(factors[c]))

    valid_count = z.notna().sum(axis=1)
    weighted = z.fillna(0.0) * pd.Series(weights)[cols]
    total_w = pd.Series(weights)[cols].abs().sum()
    raw_score = weighted.sum(axis=1) / total_w

    out = factors.copy()
    out["valid_factors"] = valid_count
    out["score"] = raw_score.where(valid_count >= min_valid_factors)
    return out.sort_values("score", ascending=False)


def top_n(scored: pd.DataFrame, n: int = 50) -> pd.DataFrame:
    """取前 N 名"""
    return scored.dropna(subset=["score"]).head(n)
