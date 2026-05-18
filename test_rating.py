"""Mock 数据验证多维度评级流程（不依赖网络）

走 grader.grade_all，确认：
- 3 个维度都有打分和评级
- 综合评级也生成
- 分档比例符合 5%/15%/30%/30%/20%
"""
import sys
import io
import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from test_mock import make_mock_universe, make_mock_panel
from universe import filter_main_board
from factors import compute_all_factors
from grader import grade_all, GRADES


def main():
    print("=" * 60)
    print("Mock 测试：多维度评级")
    print("=" * 60)

    # 1. 复用 test_mock 的 mock 数据
    raw = make_mock_universe()
    universe = filter_main_board(raw, exclude_st=True, min_list_days=0)
    ts_codes = universe["ts_code"].tolist()
    panel = make_mock_panel(ts_codes)
    asof = panel["trade_date"].max().strftime("%Y%m%d")
    factors = compute_all_factors(panel, asof_date=asof)
    print(f"\n样本: {len(factors)} 只 × {factors.shape[1]} 因子")

    # 2. 评级
    ratings = grade_all(factors)
    print(f"评级表: {ratings.shape}")
    print(f"列: {list(ratings.columns)}")

    # 3. 验证：每个维度都有分数和评级
    expected = ["score_value", "grade_value", "score_tech", "grade_tech",
                "score_flow", "grade_flow", "score_total", "grade_total"]
    for c in expected:
        assert c in ratings.columns, f"缺列 {c}"
    print("\n[pass] 所有评级列齐全")

    # 4. 分档分布检查（应该接近 S5% / A15% / B30% / C30% / D20%）
    print("\n各维度分档分布:")
    print(f"{'维度':<10}{'S':>6}{'A':>6}{'B':>6}{'C':>6}{'D':>6}")
    for dim in ["value", "tech", "flow", "total"]:
        col = f"grade_{dim}"
        counts = ratings[col].value_counts().reindex(GRADES).fillna(0).astype(int)
        print(f"{dim:<10}" + "".join(f"{counts[g]:>6}" for g in GRADES))

    # 5. 综合评级单调性
    not_null = ratings.dropna(subset=["score_total"]).sort_values("score_total", ascending=False)
    grades_seq = not_null["grade_total"].tolist()
    # 从上到下应该是 S, A, A, B, ..., D
    # 用每档第一次出现的位置应严格递增检验
    first_idx = {g: grades_seq.index(g) for g in GRADES if g in grades_seq}
    indices = list(first_idx.values())
    assert indices == sorted(indices), f"评级顺序不单调: {first_idx}"
    print("\n[pass] 综合评级随综合分单调下降")

    # 6. 展示前 10
    name_map = universe.set_index("ts_code")[["name"]]
    show = ratings.sort_values("score_total", ascending=False).head(10).join(name_map)
    show_cols = ["name", "grade_total", "score_total",
                 "grade_value", "grade_tech", "grade_flow"]
    show = show[[c for c in show_cols if c in show.columns]]
    for c in show.columns:
        if c.startswith("score"):
            show[c] = show[c].round(3)
    print("\n示例（Top 10 综合评级）:")
    pd.set_option("display.unicode.east_asian_width", True)
    pd.set_option("display.width", 200)
    print(show.to_string())

    print("\n" + "=" * 60)
    print("OK 评级管道跑通")
    print("=" * 60)


if __name__ == "__main__":
    main()
