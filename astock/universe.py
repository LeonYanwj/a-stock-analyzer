"""股票池筛选：沪深主板，排除创业板/科创板/北交所/ST/次新"""
import pandas as pd
from datetime import datetime, timedelta


MAIN_BOARD_PREFIXES = (
    "600", "601", "603", "605",  # 沪市主板
    "000", "001", "002",          # 深市主板（002 中小板已于 2021 年并入）
)


def filter_main_board(stock_list: pd.DataFrame,
                      exclude_st: bool = True,
                      min_list_days: int = 365,
                      asof_date=None) -> pd.DataFrame:
    """从 tushare stock_basic 结果中筛出沪深主板股票

    Args:
        stock_list: 含 ts_code/symbol/name/list_date 的 DataFrame
        exclude_st: 是否剔除 ST/*ST/退市股
        min_list_days: 上市天数下限，过滤次新股
    """
    df = stock_list.copy()
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)

    is_main = df["symbol"].str.startswith(MAIN_BOARD_PREFIXES)
    df = df[is_main]

    if exclude_st:
        name_upper = df["name"].fillna("").str.upper()
        is_st = name_upper.str.contains("ST") | name_upper.str.contains("退")
        if "is_st" in df.columns:
            is_st = is_st | (pd.to_numeric(df["is_st"], errors="coerce").fillna(0) == 1)
        df = df[~is_st]

    if min_list_days > 0 and "list_date" in df.columns:
        raw = df["list_date"]
        compact = raw.astype(str).str.replace("-", "", regex=False)
        ld = pd.to_datetime(compact, format="%Y%m%d", errors="coerce")
        if ld.notna().any():
            anchor = pd.to_datetime(asof_date) if asof_date is not None else datetime.now()
            cutoff = anchor - timedelta(days=min_list_days)
            # 上市日缺失不再静默放行，避免次新股绕过过滤。
            df = df[ld <= cutoff]
            df["list_date"] = ld

    return df.reset_index(drop=True)


def get_universe(fetcher, exclude_st: bool = True, min_list_days: int = 365,
                 asof_date=None) -> pd.DataFrame:
    """获取沪深主板股票池"""
    all_stocks = fetcher.get_stock_list(exchange="", list_status="L")
    return filter_main_board(
        all_stocks, exclude_st=exclude_st,
        min_list_days=min_list_days, asof_date=asof_date)
