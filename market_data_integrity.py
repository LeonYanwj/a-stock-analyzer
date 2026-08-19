"""全市场因子输入的数据完整性约束。"""
from typing import Iterable


class MarketDataIntegrityError(RuntimeError):
    """市场截面数据不满足一只证券一条记录的约束。"""


def _sample_codes(codes: Iterable, limit: int = 5) -> str:
    values = list(dict.fromkeys(str(code) for code in codes))
    sample = ", ".join(values[:limit])
    return f"{sample} 等 {len(values)} 只" if len(values) > limit else sample


def one_row_per_ts_code(frame, columns: list, source_name: str):
    """返回可安全做 ``many_to_one`` 合并的证券截面数据。

    完全相同的重复行是数据源传输重复，可安全折叠；同一 ``ts_code`` 的字段
    值彼此冲突时不能随意选一条，直接抛出带来源和样例代码的中文业务异常。
    """
    if "ts_code" not in columns:
        raise MarketDataIntegrityError(f"{source_name}缺少 ts_code，无法合并因子数据")
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise MarketDataIntegrityError(
            f"{source_name}缺少必要字段：{', '.join(missing)}"
        )
    result = frame.loc[:, columns].copy()
    if result.empty:
        return result
    result["ts_code"] = result["ts_code"].astype(str).str.strip()
    blank_codes = result["ts_code"].eq("") | result["ts_code"].eq("nan")
    if blank_codes.any():
        raise MarketDataIntegrityError(f"{source_name}存在空证券代码，无法完成市场扫描")

    duplicated = result["ts_code"].duplicated(keep=False)
    if not duplicated.any():
        return result

    value_columns = [column for column in columns if column != "ts_code"]
    conflicts = []
    for ts_code, group in result.loc[duplicated].groupby("ts_code", sort=False):
        # DataFrame.drop_duplicates 将两个 NaN 视为相同值，符合截面缺失值的语义。
        if len(group.loc[:, value_columns].drop_duplicates()) > 1:
            conflicts.append(ts_code)
    if conflicts:
        raise MarketDataIntegrityError(
            f"{source_name}存在同一证券的冲突记录：{_sample_codes(conflicts)}；"
            "请刷新该数据源后重试"
        )
    return result.drop_duplicates(subset=["ts_code"], keep="last").copy()


def ensure_unique_panel(panel) -> None:
    """在进入因子计算前确保每个证券-交易日组合唯一。"""
    required = ["ts_code", "trade_date"]
    missing = [column for column in required if column not in panel.columns]
    if missing:
        raise MarketDataIntegrityError(
            f"因子输入日线缺少必要字段：{', '.join(missing)}"
        )
    duplicate = panel.duplicated(required, keep=False)
    if duplicate.any():
        codes = panel.loc[duplicate, "ts_code"].tolist()
        raise MarketDataIntegrityError(
            f"因子输入日线存在重复证券-交易日记录：{_sample_codes(codes)}；"
            "请检查市场截面数据源"
        )
