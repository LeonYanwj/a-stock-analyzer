"""数据获取器（AKShare 实现）

对外字段与原 Tushare 版保持一致（英文），上层无感知。
"""
import os
# AKShare 走国内东财/新浪源，无需代理；在进程内禁用代理避免 ProxyError
# 仅影响本 Python 进程，不修改系统环境变量
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

import time
import pandas as pd
import akshare as ak

from config import CACHE_DIR


def symbol_to_ts_code(symbol: str) -> str:
    """6 位代码 -> Tushare 风格 ts_code"""
    s = str(symbol).zfill(6)
    if s.startswith(("6", "9")):
        return f"{s}.SH"
    return f"{s}.SZ"


def ts_code_to_symbol(ts_code: str) -> str:
    """ts_code -> 6 位代码"""
    return str(ts_code).split(".")[0].zfill(6)


# AKShare spot 字段 -> 统一英文
_SPOT_RENAME = {
    "代码": "symbol",
    "名称": "name",
    "最新价": "close",
    "涨跌幅": "pct_chg",
    "成交量": "vol",
    "成交额": "amount",
    "换手率": "turnover_rate",
    "市盈率-动态": "pe_ttm",
    "市净率": "pb",
    "总市值": "total_mv",
    "流通市值": "circ_mv",
    "量比": "volume_ratio",
}

# AKShare hist 字段 -> 统一英文
_HIST_RENAME = {
    "日期": "trade_date",
    "股票代码": "symbol",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "vol",
    "成交额": "amount",
    "振幅": "amplitude",
    "涨跌幅": "pct_chg",
    "涨跌额": "change",
    "换手率": "turnover_rate",
}


class DataFetcher:
    """A 股数据获取器（AKShare 后端）

    token 参数保留以兼容旧调用，AKShare 无须 token。
    """

    def __init__(self, token=None):
        os.makedirs(CACHE_DIR, exist_ok=True)
        self._snapshot_cache = None  # 进程内 spot 快照缓存

    def _cache_path(self, name):
        return os.path.join(CACHE_DIR, f"{name}.csv")

    def _load_cache(self, name):
        path = self._cache_path(name)
        if not os.path.exists(path):
            return None
        header = pd.read_csv(path, nrows=0).columns.tolist()
        parse_dates = ["trade_date"] if "trade_date" in header else False
        return pd.read_csv(path, parse_dates=parse_dates)

    def _save_cache(self, name, df):
        df.to_csv(self._cache_path(name), index=False)

    # ------------------------------------------------------------------
    # 全市场截面快照
    # ------------------------------------------------------------------
    def get_market_snapshot(self, use_cache: bool = True) -> pd.DataFrame:
        """全市场实时快照（含 PE/PB/市值/换手率）"""
        if use_cache and self._snapshot_cache is not None:
            return self._snapshot_cache

        df = ak.stock_zh_a_spot_em()
        df = df.rename(columns=_SPOT_RENAME)
        # 只保留我们要用的列
        keep = [c for c in
                ["symbol", "name", "close", "pct_chg", "vol", "amount",
                 "turnover_rate", "pe_ttm", "pb", "total_mv", "circ_mv", "volume_ratio"]
                if c in df.columns]
        df = df[keep].copy()
        df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        df["ts_code"] = df["symbol"].map(symbol_to_ts_code)

        # 数值列强制转 float（AKShare 偶尔混入 "-"）
        for c in ["close", "pct_chg", "vol", "amount", "turnover_rate",
                  "pe_ttm", "pb", "total_mv", "circ_mv", "volume_ratio"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        self._snapshot_cache = df
        return df

    # ------------------------------------------------------------------
    # 股票列表（兼容旧接口，由 spot 派生）
    # ------------------------------------------------------------------
    def get_stock_list(self, exchange="", list_status="L"):
        """全市场股票列表，字段与 Tushare 接口对齐"""
        cache_name = "stock_list_ak"
        cached = self._load_cache(cache_name)
        if cached is not None:
            return cached

        spot = self.get_market_snapshot()
        df = spot[["ts_code", "symbol", "name"]].copy()
        df["area"] = ""
        df["industry"] = ""
        df["list_date"] = ""  # AKShare spot 不含上市日，universe 已做安全降级
        self._save_cache(cache_name, df)
        return df

    # ------------------------------------------------------------------
    # 单股历史日线（带文件缓存）
    # ------------------------------------------------------------------
    def get_daily(self, ts_code, start_date, end_date, adjust="qfq"):
        """获取单股历史日线，字段对齐 Tushare daily

        Args:
            ts_code: 如 '000001.SZ'
            start_date / end_date: 'YYYYMMDD'
            adjust: '' 不复权 / 'qfq' 前复权 / 'hfq' 后复权
        """
        cache_name = f"daily_{ts_code}_{start_date}_{end_date}_{adjust or 'raw'}"
        cached = self._load_cache(cache_name)
        if cached is not None:
            return cached

        symbol = ts_code_to_symbol(ts_code)
        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
        except Exception as e:
            print(f"  [warn] {ts_code} 拉取失败: {e}")
            return pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()

        df = df.rename(columns=_HIST_RENAME)
        df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        df["ts_code"] = ts_code
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values("trade_date").reset_index(drop=True)
        for c in ["open", "close", "high", "low", "vol", "amount",
                  "amplitude", "pct_chg", "change", "turnover_rate"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        self._save_cache(cache_name, df)
        return df

    # ------------------------------------------------------------------
    # 交易日历
    # ------------------------------------------------------------------
    def get_trade_dates(self, start_date, end_date):
        """返回 [start, end] 区间内的交易日列表（YYYYMMDD 升序）"""
        cache_name = "trade_cal_ak"
        cached = self._load_cache(cache_name)
        if cached is not None:
            cal = cached
        else:
            cal = ak.tool_trade_date_hist_sina()
            self._save_cache(cache_name, cal)

        cal["trade_date"] = pd.to_datetime(cal["trade_date"])
        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date)
        mask = (cal["trade_date"] >= start) & (cal["trade_date"] <= end)
        return cal.loc[mask, "trade_date"].dt.strftime("%Y%m%d").tolist()

    # 兼容旧名
    def get_trade_cal(self, start_date, end_date, exchange="SSE"):
        return self.get_trade_dates(start_date, end_date)

    # ------------------------------------------------------------------
    # 兼容性占位：原 Tushare 全市场接口在 AKShare 下用 spot + history 替代
    # ------------------------------------------------------------------
    def get_daily_all(self, trade_date):
        raise NotImplementedError(
            "AKShare 后端无指定历史日的全市场接口；"
            "请改用 get_market_snapshot()（当日） + get_daily() 循环拉历史"
        )

    def get_daily_basic_all(self, trade_date):
        raise NotImplementedError(
            "AKShare 后端无指定历史日的全市场基本面接口；请用 get_market_snapshot()"
        )
