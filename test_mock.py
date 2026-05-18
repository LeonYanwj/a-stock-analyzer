"""Mock 数据验证多因子选股全流程（不依赖网络）

构造合成数据走 universe -> factors -> selector，
检查筛选数量、因子非空率、打分排序是否合理。
"""
import sys
import io
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from universe import filter_main_board
from factors import compute_all_factors
from selector import score, top_n


N_DAYS = 90
SEED = 42
np.random.seed(SEED)


def make_mock_universe():
    """构造覆盖各板块的虚拟 stock_list"""
    codes, names = [], []
    # 沪主板 80 只（其中 0/1 号设为 ST 测试过滤）
    for i in range(80):
        codes.append(f"6{i:05d}")
        names.append(f"沪主板{i:02d}")
    names[0] = "ST沪主板00"
    names[1] = "*ST沪主板01"
    # 深主板 80
    for i in range(80):
        codes.append(f"00{i:04d}")
        names.append(f"深主板{i:02d}")
    # 创业板 20（应被剔除）
    for i in range(20):
        codes.append(f"3000{i:02d}")
        names.append(f"创业板{i:02d}")
    # 科创板 10（应被剔除）
    for i in range(10):
        codes.append(f"68800{i}")
        names.append(f"科创板{i:02d}")

    df = pd.DataFrame({
        "symbol": [c.zfill(6) for c in codes],
        "name": names,
        "list_date": "",
    })
    df["ts_code"] = [
        f"{s}.SH" if s.startswith(("6", "9")) else f"{s}.SZ"
        for s in df["symbol"]
    ]
    return df


def make_mock_panel(ts_codes, n_days=N_DAYS):
    """合成 panel：随机游走价格 + 截面 PE/PB/市值"""
    end = datetime.now()
    dates = pd.date_range(end=end, periods=n_days, freq="B")  # 工作日

    n = len(ts_codes)
    # 给每只股票分配一个"内在动量"用于后续验证排序合理性
    base_drift = np.random.normal(0, 0.001, n)

    frames = []
    for i, tc in enumerate(ts_codes):
        rets = np.random.normal(base_drift[i], 0.02, n_days)
        close = 10 * np.exp(np.cumsum(rets))
        frames.append(pd.DataFrame({
            "ts_code": tc,
            "trade_date": dates,
            "close": close,
            "pct_chg": rets * 100,
            "vol": np.random.lognormal(15, 0.5, n_days),
            "amount": np.random.lognormal(20, 0.5, n_days),
            "turnover_rate": np.abs(np.random.normal(1.5, 0.8, n_days)),
        }))
    panel = pd.concat(frames, ignore_index=True)

    # 截面字段
    pe_ttm = np.random.lognormal(2.5, 0.5, n)
    pb = np.random.lognormal(0.5, 0.5, n)
    total_mv = np.random.lognormal(13, 1.0, n)
    circ_mv = total_mv * np.random.uniform(0.6, 0.95, n)
    # 资金流（亿元级量级；服从对称分布，有进有出）
    fund_inflow = np.random.lognormal(8, 1.0, n)
    fund_outflow = np.random.lognormal(8, 1.0, n)
    fund_net = fund_inflow - fund_outflow

    # 注入若干异常值检验防御
    pe_ttm[np.random.choice(n, max(1, n // 20), replace=False)] = np.nan
    pb[np.random.choice(n, max(1, n // 40), replace=False)] = -1.0  # 负 PB

    snap = pd.DataFrame({
        "ts_code": ts_codes,
        "pe_ttm": pe_ttm,
        "pb": pb,
        "total_mv": total_mv,
        "circ_mv": circ_mv,
        "fund_inflow": fund_inflow,
        "fund_outflow": fund_outflow,
        "fund_net": fund_net,
    })
    return panel.merge(snap, on="ts_code", how="left")


def main():
    print("=" * 60)
    print("Mock 测试：多因子选股全流程")
    print("=" * 60)

    # 1. 股票池筛选
    print("\n[1/4] universe 筛选...")
    raw = make_mock_universe()
    print(f"  mock 原始: {len(raw)} 只 (沪80 + 深80 + 创业20 + 科创10 = 190)")
    universe = filter_main_board(raw, exclude_st=True, min_list_days=0)
    print(f"  筛选后:    {len(universe)} 只")
    assert len(universe) == 158, f"预期 158 (主板160 - 2 ST), 实际 {len(universe)}"
    # 验证：没有 3/6开头(科创)/30开头(创业)
    bad = universe["symbol"].str.startswith(("300", "301", "688", "689"))
    assert not bad.any(), "仍含创业板/科创板"
    bad_st = universe["name"].str.contains("ST")
    assert not bad_st.any(), "仍含 ST"
    print("  [pass] 沪深主板筛选正确，剔除创业板/科创板/ST")

    ts_codes = universe["ts_code"].tolist()

    # 2. 构造 panel
    print(f"\n[2/4] 构造 mock 行情面板 ({len(ts_codes)} 只 × {N_DAYS} 工作日)...")
    panel = make_mock_panel(ts_codes)
    print(f"  panel: {len(panel)} 行")
    assert {"close", "pe_ttm", "pb", "circ_mv", "turnover_rate"}.issubset(panel.columns)
    print("  [pass] 关键字段齐全")

    # 3. 因子计算
    print("\n[3/4] 因子计算...")
    asof = panel["trade_date"].max().strftime("%Y%m%d")
    factors = compute_all_factors(panel, asof_date=asof)
    print(f"  因子表: {factors.shape[0]} 只 × {factors.shape[1]} 因子")
    print("  非空率:")
    fill_rate = factors.notna().sum() / len(factors)
    print(fill_rate.round(3).to_string())
    assert factors.shape[0] == len(ts_codes)
    assert (fill_rate >= 0.9).all() or (fill_rate.drop(["ep_ttm", "bp"]) >= 0.99).all(), \
        "非空率异常（除 PE/PB 注入异常外应接近 100%）"
    print("  [pass] 因子计算无异常")

    # 4. 打分排序
    print("\n[4/4] 打分排序...")
    scored = score(factors)
    picks = top_n(scored, n=20)
    name_map = universe.set_index("ts_code")[["name"]]
    out = picks.join(name_map, how="left")[["name", "score", "valid_factors"]]
    print(f"  Top 20:")
    print(out.round(4).to_string())

    # 排序单调性
    assert scored["score"].dropna().is_monotonic_decreasing, "score 列未降序"
    print("\n  [pass] 排序单调降序")

    print("\n" + "=" * 60)
    print("OK 全流程跑通（不依赖网络）")
    print("=" * 60)


if __name__ == "__main__":
    main()
