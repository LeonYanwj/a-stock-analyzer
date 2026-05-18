"""单股评级（基于经验阈值，不依赖全市场参照）

设计：每个因子独立打 1-5 星，每个维度内取均分，整体取加权均分。
星级 → 等级映射：
    ≥ 4.5  S
    ≥ 3.8  A
    ≥ 3.0  B
    ≥ 2.3  C
    <  2.3 D
"""
from dataclasses import dataclass, field
from typing import Optional, List
import numpy as np
import pandas as pd


# 维度的因子组成 + 权重
DIM_FACTORS = {
    "tech": {
        "label": "量价面",
        "factors": ["mom_30", "reversal_5", "low_vol", "liquidity"],
    },
    "value": {
        "label": "价值面",
        "factors": ["pe_ttm", "pb"],
    },
    "flow": {
        "label": "资金面",
        "factors": ["fund_net_5d", "inflow_ratio_5d"],
    },
}

# 综合评级时各维度权重
DIM_WEIGHTS = {"tech": 1.0, "value": 1.0, "flow": 1.2}


def _stars(n: int) -> str:
    return "★" * n + "☆" * (5 - n)


# ----------------------------------------------------------------------
# 单因子打分函数：返回 (star_1_to_5, 文字描述)；输入 NaN/None -> (None, "无数据")
# ----------------------------------------------------------------------
def score_mom_30(v):
    if v is None or pd.isna(v):
        return None, "无数据"
    pct = v * 100
    if v >= 0.30:    return 5, f"近30日大涨 +{pct:.1f}%"
    if v >= 0.10:    return 4, f"近30日上涨 +{pct:.1f}%"
    if v >= -0.05:   return 3, f"近30日震荡 {pct:+.1f}%"
    if v >= -0.15:   return 2, f"近30日小跌 {pct:.1f}%"
    return 1, f"近30日大跌 {pct:.1f}%"


def score_reversal_5(v):
    """5日反转：短期跌了再涨概率高，所以小跌得分高"""
    if v is None or pd.isna(v):
        return None, "无数据"
    pct = v * 100
    if v <= -0.08:   return 5, f"近5日深跌 {pct:.1f}%（强反弹预期）"
    if v <= -0.03:   return 4, f"近5日回调 {pct:.1f}%（反弹机会）"
    if v <= 0.03:    return 3, f"近5日横盘 {pct:+.1f}%"
    if v <= 0.08:    return 2, f"近5日上涨 +{pct:.1f}%（短线追高）"
    return 1, f"近5日急涨 +{pct:.1f}%（追高风险）"


def score_low_vol(v):
    """v = 20 日日收益年化波动率（小数）"""
    if v is None or pd.isna(v):
        return None, "无数据"
    pct = v * 100
    if v <= 0.20:    return 5, f"年化波动 {pct:.1f}%（极低波）"
    if v <= 0.30:    return 4, f"年化波动 {pct:.1f}%（低波）"
    if v <= 0.45:    return 3, f"年化波动 {pct:.1f}%（中波）"
    if v <= 0.60:    return 2, f"年化波动 {pct:.1f}%（高波）"
    return 1, f"年化波动 {pct:.1f}%（极高波）"


def score_liquidity(v):
    """v = 20 日均换手率（百分比，例如 3.5 表 3.5%）"""
    if v is None or pd.isna(v):
        return None, "无数据"
    if v >= 5:       return 5, f"20日均换手 {v:.2f}%（极活跃）"
    if v >= 3:       return 4, f"20日均换手 {v:.2f}%（活跃）"
    if v >= 1.5:     return 3, f"20日均换手 {v:.2f}%（一般）"
    if v >= 0.5:     return 2, f"20日均换手 {v:.2f}%（偏淡）"
    return 1, f"20日均换手 {v:.2f}%（冷门）"


def score_pe(v):
    if v is None or pd.isna(v):
        return None, "PE 缺失（亏损或未披露）"
    if v <= 0:       return 1, f"PE={v:.1f}（亏损）"
    if v <= 15:      return 5, f"PE={v:.1f}（低估）"
    if v <= 25:      return 4, f"PE={v:.1f}（合理）"
    if v <= 40:      return 3, f"PE={v:.1f}（略高）"
    if v <= 80:      return 2, f"PE={v:.1f}（偏高）"
    return 1, f"PE={v:.1f}（过高）"


def score_pb(v):
    if v is None or pd.isna(v):
        return None, "PB 缺失"
    if v <= 0:       return 1, f"PB={v:.2f}（异常）"
    if v <= 1.0:     return 5, f"PB={v:.2f}（破净）"
    if v <= 2.0:     return 4, f"PB={v:.2f}（合理）"
    if v <= 4.0:     return 3, f"PB={v:.2f}（略高）"
    if v <= 8.0:     return 2, f"PB={v:.2f}（偏高）"
    return 1, f"PB={v:.2f}（过高）"


def score_fund_net_5d(v):
    """v = 5日主力净流入（元）。亿元量级"""
    if v is None or pd.isna(v):
        return None, "资金流数据缺失"
    yi = v / 1e8
    if v >= 5e8:     return 5, f"5日净流入 +{yi:.2f}亿（主力强力买入）"
    if v >= 1e8:     return 4, f"5日净流入 +{yi:.2f}亿（主力买入）"
    if v >= -1e8:    return 3, f"5日净流入 {yi:+.2f}亿（多空均衡）"
    if v >= -5e8:    return 2, f"5日净流出 {yi:.2f}亿（主力卖出）"
    return 1, f"5日净流出 {yi:.2f}亿（主力大幅减仓）"


def score_inflow_ratio_5d(v):
    """v = (流入-流出)/(流入+流出)，范围 [-1, 1]"""
    if v is None or pd.isna(v):
        return None, "资金流比例缺失"
    pct = v * 100
    if v >= 0.15:    return 5, f"净流入占比 +{pct:.1f}%（资金压倒性流入）"
    if v >= 0.05:    return 4, f"净流入占比 +{pct:.1f}%（资金净流入）"
    if v >= -0.05:   return 3, f"净流入占比 {pct:+.1f}%（资金平衡）"
    if v >= -0.15:   return 2, f"净流入占比 {pct:.1f}%（资金净流出）"
    return 1, f"净流入占比 {pct:.1f}%（资金压倒性流出）"


SCORERS = {
    "mom_30":          score_mom_30,
    "reversal_5":      score_reversal_5,
    "low_vol":         score_low_vol,
    "liquidity":       score_liquidity,
    "pe_ttm":          score_pe,
    "pb":              score_pb,
    "fund_net_5d":     score_fund_net_5d,
    "inflow_ratio_5d": score_inflow_ratio_5d,
}


# ----------------------------------------------------------------------
# 评级数据结构
# ----------------------------------------------------------------------
@dataclass
class FactorScore:
    key: str
    stars: Optional[int]
    desc: str
    raw_value: float


@dataclass
class DimensionScore:
    key: str
    label: str
    factors: List[FactorScore] = field(default_factory=list)

    @property
    def stars(self) -> Optional[float]:
        valid = [f.stars for f in self.factors if f.stars is not None]
        if not valid:
            return None
        return sum(valid) / len(valid)


@dataclass
class StockRating:
    ts_code: str
    name: str
    asof: str
    dimensions: List[DimensionScore]
    raw_values: dict   # {因子名: 原始数值}，用于追溯

    @property
    def overall_stars(self) -> Optional[float]:
        weighted, total_w = 0.0, 0.0
        for d in self.dimensions:
            if d.stars is not None:
                w = DIM_WEIGHTS.get(d.key, 1.0)
                weighted += d.stars * w
                total_w += w
        if total_w == 0:
            return None
        return weighted / total_w

    @property
    def grade(self) -> str:
        s = self.overall_stars
        if s is None:    return "N/A"
        if s >= 4.5:     return "S"
        if s >= 3.8:     return "A"
        if s >= 3.0:     return "B"
        if s >= 2.3:     return "C"
        return "D"

    def to_report(self) -> str:
        """生成可读的文字评级报告"""
        lines = []
        sep = "=" * 60
        lines.append(sep)
        title = f"{self.ts_code} {self.name or ''}".strip()
        lines.append(f"  {title}    截面日: {self.asof}")
        lines.append(sep)

        overall = self.overall_stars
        if overall is None:
            lines.append("  综合评级: N/A（所有维度数据缺失）")
        else:
            lines.append(f"  综合评级: {self.grade}   "
                         f"{_stars(round(overall))}   ({overall:.2f} / 5.00)")
        lines.append("")

        for d in self.dimensions:
            avg = d.stars
            if avg is None:
                lines.append(f"【{d.label}】 数据不可用")
            else:
                lines.append(f"【{d.label}】 {_stars(round(avg))}   ({avg:.2f}/5)")
            for f in d.factors:
                if f.stars is None:
                    lines.append(f"   · {f.key:<18}  -    {f.desc}")
                else:
                    lines.append(f"   · {f.key:<18}  {_stars(f.stars)}  {f.desc}")
            lines.append("")

        lines.append(sep)
        return "\n".join(lines)


# ----------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------
def grade_single(ts_code: str, name: str, asof: str, factor_values: dict) -> StockRating:
    """从一组因子原始值生成评级

    factor_values: 例如
        {"mom_30": 0.18, "reversal_5": -0.02, "low_vol": 0.28, "liquidity": 3.5,
         "pe_ttm": 22, "pb": 2.8, "fund_net_5d": 2.3e8, "inflow_ratio_5d": 0.12}
    """
    dims = []
    for dim_key, dim_info in DIM_FACTORS.items():
        d = DimensionScore(key=dim_key, label=dim_info["label"])
        for fkey in dim_info["factors"]:
            v = factor_values.get(fkey)
            stars, desc = SCORERS[fkey](v)
            d.factors.append(FactorScore(key=fkey, stars=stars, desc=desc, raw_value=v))
        dims.append(d)
    return StockRating(ts_code=ts_code, name=name, asof=asof,
                       dimensions=dims, raw_values=factor_values)
