"""数据获取器（AKShare 实现）

对外字段与原 Tushare 版保持一致（英文），上层无感知。
"""
import os
# AKShare 走国内东财/新浪源，无需代理；在进程内禁用代理避免 ProxyError
# 仅影响本 Python 进程，不修改系统环境变量
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

import time
import numpy as np
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


def ts_code_to_sina_symbol(ts_code: str) -> str:
    """ts_code -> 新浪/腾讯格式 ('sh600000' / 'sz000001')"""
    base = ts_code_to_symbol(ts_code)
    suffix = str(ts_code).split(".")[-1].lower()
    if suffix == "sh":
        return f"sh{base}"
    if suffix == "sz":
        return f"sz{base}"
    return base


# AKShare spot 字段 -> 统一英文（兼容东财和新浪源）
_SPOT_RENAME = {
    "代码": "symbol",
    "名称": "name",
    "最新价": "close",
    "涨跌幅": "pct_chg",
    "涨跌额": "change",
    "成交量": "vol",
    "成交额": "amount",
    "换手率": "turnover_rate",
    "市盈率-动态": "pe_ttm",
    "市净率": "pb",
    "总市值": "total_mv",
    "流通市值": "circ_mv",
    "量比": "volume_ratio",
    "昨收": "pre_close",
    "今开": "open",
    "最高": "high",
    "最低": "low",
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
        self._fund_flow_cache = {}   # 资金流快照缓存（按窗口）

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
    def get_market_snapshot(self, use_cache: bool = True, source: str = "auto") -> pd.DataFrame:
        """全市场实时快照

        Args:
            use_cache: 是否使用进程内缓存
            source: 'em' 东财（字段全含 PE/PB/市值，但部分网络下被封）
                    'sina' 新浪（只有量价字段，国内访问稳定）
                    'auto' 自动：先东财，失败回退新浪
        """
        if use_cache and self._snapshot_cache is not None:
            return self._snapshot_cache

        df = None
        used = None
        if source in ("em", "auto"):
            try:
                df = ak.stock_zh_a_spot_em()
                used = "em"
            except Exception as e:
                if source == "em":
                    raise
                print(f"  [warn] 东财 spot 失败，回退新浪: {type(e).__name__}")

        if df is None or df.empty:
            df = ak.stock_zh_a_spot()
            used = "sina"

        df = df.rename(columns=_SPOT_RENAME)

        # 新浪源 symbol 带 'sh'/'sz'/'bj' 前缀，东财源不带
        raw = df["symbol"].astype(str)
        has_prefix = raw.str[:2].isin(["sh", "sz", "bj"]).any()
        if has_prefix:
            # 剔除北交所
            df = df[~raw.str.startswith("bj")].reset_index(drop=True)
            df["symbol"] = df["symbol"].astype(str).str[2:].str.zfill(6)
        else:
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)

        keep = [c for c in
                ["symbol", "name", "close", "pct_chg", "vol", "amount",
                 "turnover_rate", "pe_ttm", "pb", "total_mv", "circ_mv", "volume_ratio"]
                if c in df.columns]
        df = df[keep].copy()
        df["ts_code"] = df["symbol"].map(symbol_to_ts_code)

        for c in ["close", "pct_chg", "vol", "amount", "turnover_rate",
                  "pe_ttm", "pb", "total_mv", "circ_mv", "volume_ratio"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        print(f"  [snapshot] source={used}, cols={list(df.columns)}, rows={len(df)}")
        self._snapshot_cache = df
        return df

    # ------------------------------------------------------------------
    # 同花顺财务摘要 + 量价齐升排行
    # ------------------------------------------------------------------
    def get_stock_financial_abstract(self, ts_code) -> pd.DataFrame:
        """单股财务摘要（同花顺，含 ROE/毛利率/净利率历史）

        解析中文单位（"%/万/亿"）成 float。
        百分比字段保留为"百分比数值"（如 23.85 表示 23.85%）。
        """
        cache_name = f"finabs_{ts_code}"
        cached = self._load_cache(cache_name)
        if cached is not None:
            return cached

        symbol = ts_code_to_symbol(ts_code)
        try:
            df = ak.stock_financial_abstract_ths(symbol=symbol, indicator="按报告期")
        except Exception as e:
            print(f"  [warn] {ts_code} 财务拉取失败: {type(e).__name__}: {str(e)[:80]}")
            return pd.DataFrame()
        if df is None or df.empty:
            return pd.DataFrame()

        def _parse(v):
            if v is False or v is None or pd.isna(v):
                return np.nan
            t = str(v).strip()
            if t in ("", "-", "--", "False"):
                return np.nan
            if t.endswith("%"):
                try:    return float(t[:-1])
                except: return np.nan
            mult = 1.0
            if t.endswith("亿"):  mult, t = 1e8, t[:-1]
            elif t.endswith("万"): mult, t = 1e4, t[:-1]
            try:    return float(t) * mult
            except: return np.nan

        # 数值列解析（保留"报告期"为字符串）
        for c in df.columns:
            if c != "报告期":
                df[c] = df[c].map(_parse)
        if "报告期" in df.columns:
            df["报告期"] = pd.to_datetime(df["报告期"], errors="coerce")
            df = df.sort_values("报告期").reset_index(drop=True)
        self._save_cache(cache_name, df)
        return df

    def get_stock_rank_lxsz(self, use_cache: bool = True) -> pd.DataFrame:
        """全市场量价齐升排行（同花顺，一次拉所有上榜股）

        Returns: ts_code, symbol, 连涨天数, 连续涨跌幅, 累计换手率, 所属行业, 收盘价
        """
        if use_cache and hasattr(self, "_lxsz_cache") and self._lxsz_cache is not None:
            return self._lxsz_cache
        try:
            df = ak.stock_rank_lxsz_ths()
        except Exception as e:
            print(f"  [warn] 量价齐升排行拉取失败: {type(e).__name__}: {str(e)[:80]}")
            self._lxsz_cache = pd.DataFrame()
            return self._lxsz_cache
        if df is None or df.empty:
            self._lxsz_cache = pd.DataFrame()
            return self._lxsz_cache

        df = df.rename(columns={
            "股票代码": "symbol",
            "股票简称": "name",
            "收盘价":   "close",
            "连涨天数": "lxsz_days",
            "连续涨跌幅": "lxsz_pct",
            "累计换手率": "lxsz_turn",
            "所属行业": "industry",
        })
        df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        df["ts_code"] = df["symbol"].map(symbol_to_ts_code)
        for c in ["close", "lxsz_days", "lxsz_pct", "lxsz_turn"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        print(f"  [lxsz] 量价齐升上榜: {len(df)} 只")
        self._lxsz_cache = df
        return df

    # ------------------------------------------------------------------
    # 消息面：个股新闻 / 公告 / 研报
    # ------------------------------------------------------------------
    def get_stock_news(self, ts_code) -> pd.DataFrame:
        """单股最近新闻（东财源，stock_news_em）

        Returns: DataFrame 含 关键词/新闻标题/新闻内容/发布时间/文章来源/新闻链接
                 失败返回空 DataFrame
        """
        symbol = ts_code_to_symbol(ts_code)
        try:
            df = ak.stock_news_em(symbol=symbol)
        except Exception as e:
            print(f"  [warn] {ts_code} 新闻拉取失败: {type(e).__name__}: {str(e)[:80]}")
            return pd.DataFrame()
        if df is None or df.empty:
            return pd.DataFrame()
        if "发布时间" in df.columns:
            df["发布时间"] = pd.to_datetime(df["发布时间"], errors="coerce")
        return df

    def get_stock_disclosure(self, ts_code, days: int = 30) -> pd.DataFrame:
        """单股近 N 天公告（巨潮 stock_zh_a_disclosure_report_cninfo）

        Returns: DataFrame 含 代码/简称/公告标题/公告时间/公告链接
        """
        from datetime import datetime, timedelta
        symbol = ts_code_to_symbol(ts_code)
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        try:
            df = ak.stock_zh_a_disclosure_report_cninfo(
                symbol=symbol, start_date=start, end_date=end
            )
        except Exception as e:
            print(f"  [warn] {ts_code} 公告拉取失败: {type(e).__name__}: {str(e)[:80]}")
            return pd.DataFrame()
        if df is None or df.empty:
            return pd.DataFrame()
        if "公告时间" in df.columns:
            df["公告时间"] = pd.to_datetime(df["公告时间"], errors="coerce")
        return df

    def get_stock_research(self, ts_code) -> pd.DataFrame:
        """单股研报（东财 stock_research_report_em）

        Returns: 含 东财评级、机构、报告名称、日期 等
        """
        symbol = ts_code_to_symbol(ts_code)
        try:
            df = ak.stock_research_report_em(symbol=symbol)
        except Exception as e:
            print(f"  [warn] {ts_code} 研报拉取失败: {type(e).__name__}: {str(e)[:80]}")
            return pd.DataFrame()
        if df is None or df.empty:
            return pd.DataFrame()
        if "日期" in df.columns:
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
        return df

    # ------------------------------------------------------------------
    # 单股估值指标（PE/PB/股息率历史）
    # ------------------------------------------------------------------
    def get_stock_indicator(self, ts_code) -> pd.DataFrame:
        """单股估值指标（PE/PB/市值，东财源）

        优先级: DB -> CSV cache -> API；新拉数据自动写回 DB

        Returns:
            DataFrame: 含 PE/PB/总市值 列；失败时返回空 DataFrame
        """
        # ---- 优先级 1: DB ----
        try:
            from data.db import get_conn
            with get_conn() as conn:
                df = pd.read_sql(
                    "SELECT trade_date, pe, pe_ttm, pb, ps, total_mv, circ_mv "
                    "FROM market_valuation WHERE ts_code=%s ORDER BY trade_date ASC",
                    conn, params=(ts_code,))
                if not df.empty:
                    df["ts_code"] = ts_code
                    return df
        except Exception:
            pass

        # ---- 优先级 2: CSV cache ----
        cache_name = f"indicator_{ts_code}"
        cached = self._load_cache(cache_name)
        if cached is not None:
            return cached

        # ---- 优先级 3: API ----
        symbol = ts_code_to_symbol(ts_code)
        try:
            df = ak.stock_value_em(symbol=symbol)
        except Exception as e:
            print(f"  [warn] {ts_code} 估值指标拉取失败: {type(e).__name__}: {str(e)[:80]}")
            return pd.DataFrame()
        if df is None or df.empty:
            return pd.DataFrame()

        # 字段标准化（接口可能用中文/英文，做兼容）
        rename = {
            "数据日期": "trade_date",
            "日期":     "trade_date",
            "PE(TTM)":  "pe_ttm",
            "PE(静)":   "pe",
            "市盈率TTM": "pe_ttm",
            "市盈率":   "pe",
            "市净率":   "pb",
            "PB":       "pb",
            "总市值":   "total_mv",
        }
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
            df = df.sort_values("trade_date").reset_index(drop=True)
        for c in ["pe", "pe_ttm", "pb", "total_mv"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        self._save_cache(cache_name, df)

        # 写回 DB（不影响返回，失败 silent）
        try:
            from data.db import get_conn, upsert_valuation
            df_db = df.copy()
            df_db["ts_code"] = ts_code
            if "trade_date" in df_db.columns:
                df_db["trade_date"] = pd.to_datetime(
                    df_db["trade_date"].astype(str), errors="coerce"
                ).dt.strftime("%Y-%m-%d")
                df_db = df_db.dropna(subset=["trade_date"])
                with get_conn() as conn:
                    upsert_valuation(conn, df_db)
        except Exception:
            pass

        return df

    # ------------------------------------------------------------------
    # 资金流快照（同花顺源，全市场一次拉完）
    # ------------------------------------------------------------------
    def get_fund_flow_snapshot(self, window: str = "5日排行",
                               use_cache: bool = True) -> pd.DataFrame:
        """全市场资金流快照（同花顺源）

        Args:
            window: '即时' | '3日排行' | '5日排行' | '10日排行' | '20日排行'

        Returns:
            DataFrame: ts_code, symbol, fund_inflow, fund_outflow, fund_net
                       本接口失败时返回空 DataFrame（不抛异常，不阻塞主流程）
        """
        if use_cache and window in self._fund_flow_cache:
            return self._fund_flow_cache[window]

        try:
            df = ak.stock_fund_flow_individual(symbol=window)
        except Exception as e:
            print(f"  [warn] 同花顺资金流接口失败 ({window}): {type(e).__name__}: {str(e)[:80]}")
            return pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()

        # 字段重命名（同花顺中文字段 -> 英文，兼容不同窗口字段差异）
        # 即时:      流入资金 / 流出资金 / 净额
        # N日排行:   资金流入净额（只有净额，没有分别流入流出）
        rename_map = {
            "股票代码": "symbol",
            "股票简称": "name",
            "最新价": "close",
            "涨跌幅": "pct_chg",
            "阶段涨跌幅": "pct_chg",
            "换手率": "turnover_rate",
            "连续换手率": "turnover_rate",
            "流入资金": "fund_inflow",
            "流出资金": "fund_outflow",
            "净额": "fund_net",
            "净流入": "fund_net",
            "净流入额": "fund_net",
            "资金流入净额": "fund_net",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

        # 同花顺返回的数值是带"亿/万/%"后缀的字符串，先解析成 float
        def _parse_num(s):
            if pd.isna(s) or s is None:
                return np.nan
            t = str(s).strip()
            if t in ("", "-", "--"):
                return np.nan
            mult = 1.0
            if t.endswith("%"):
                try:
                    return float(t[:-1]) / 100.0
                except ValueError:
                    return np.nan
            if t.endswith("亿"):
                mult, t = 1e8, t[:-1]
            elif t.endswith("万"):
                mult, t = 1e4, t[:-1]
            try:
                return float(t) * mult
            except ValueError:
                return np.nan

        for c in ["close", "pct_chg", "turnover_rate",
                  "fund_inflow", "fund_outflow", "fund_net"]:
            if c in df.columns:
                df[c] = df[c].map(_parse_num)

        if "symbol" not in df.columns:
            print(f"  [warn] 同花顺资金流返回无 symbol 列，原始字段: {list(df.columns)}")
            return pd.DataFrame()

        df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        df["ts_code"] = df["symbol"].map(symbol_to_ts_code)

        # 若接口未直接给净额，由流入-流出计算
        if "fund_net" not in df.columns:
            if "fund_inflow" in df.columns and "fund_outflow" in df.columns:
                df["fund_net"] = df["fund_inflow"] - df["fund_outflow"]

        keep = [c for c in ["ts_code", "symbol", "fund_inflow", "fund_outflow", "fund_net"]
                if c in df.columns]
        df = df[keep].copy()

        print(f"  [fund_flow] window={window}, rows={len(df)}, cols={list(df.columns)}")
        self._fund_flow_cache[window] = df
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
    def get_daily(self, ts_code, start_date, end_date, adjust="qfq", source="sina"):
        """获取单股历史日线，统一英文字段

        优先级：MySQL DB（最快）-> CSV cache（兼容旧版）-> API（最后兜底）

        Args:
            ts_code: 如 '000001.SZ'
            start_date / end_date: 'YYYYMMDD'
            adjust: '' 不复权 / 'qfq' 前复权 / 'hfq' 后复权
            source: 'sina' 新浪（稳，含换手率）/ 'em' 东财（批量易反爬）/ 'tx' 腾讯（轻量）
        """
        # ---- 优先级 1: MySQL DB（如果完全覆盖请求范围）----
        try:
            from data.db import get_conn, query_daily, query_daily_coverage
            sd = pd.to_datetime(start_date if "-" in str(start_date)
                                else f"{str(start_date)[:4]}-{str(start_date)[4:6]}-{str(start_date)[6:]}")
            ed = pd.to_datetime(end_date if "-" in str(end_date)
                                else f"{str(end_date)[:4]}-{str(end_date)[4:6]}-{str(end_date)[6:]}")
            with get_conn() as conn:
                min_d, max_d = query_daily_coverage(conn, ts_code, adjust)
                if (min_d is not None and max_d is not None
                        and pd.to_datetime(min_d) <= sd
                        and pd.to_datetime(max_d) >= ed):
                    return query_daily(conn, ts_code, start_date, end_date, adjust)
        except Exception:
            pass  # DB 不可用，往下走

        # ---- 优先级 2: CSV cache（兼容旧版本）----
        cache_name = f"daily_{ts_code}_{start_date}_{end_date}_{adjust or 'raw'}"
        cached = self._load_cache(cache_name)
        if cached is not None:
            return cached

        # ---- 优先级 3: 调 API ----
        try:
            if source == "sina":
                sym = ts_code_to_sina_symbol(ts_code)
                df = ak.stock_zh_a_daily(
                    symbol=sym,
                    adjust=adjust if adjust in ("qfq", "hfq") else "",
                    start_date=start_date,
                    end_date=end_date,
                )
                df = df.rename(columns={
                    "date": "trade_date",
                    "volume": "vol",
                    "turnover": "turnover_rate",
                })
                # 新浪 turnover 是小数（0.0047 表 0.47%），统一成百分比
                if "turnover_rate" in df.columns:
                    df["turnover_rate"] = df["turnover_rate"] * 100
            elif source == "em":
                df = ak.stock_zh_a_hist(
                    symbol=ts_code_to_symbol(ts_code),
                    period="daily",
                    start_date=start_date,
                    end_date=end_date,
                    adjust=adjust,
                )
                df = df.rename(columns=_HIST_RENAME)
            elif source == "tx":
                df = ak.stock_zh_a_hist_tx(
                    symbol=ts_code_to_sina_symbol(ts_code),
                    start_date=start_date,
                    end_date=end_date,
                    adjust=adjust if adjust in ("qfq", "hfq") else "",
                )
                df = df.rename(columns={"date": "trade_date"})
            else:
                raise ValueError(f"未知 source: {source}")
        except Exception as e:
            print(f"  [warn] {ts_code} 拉取失败 ({source}): {type(e).__name__}: {str(e)[:80]}")
            return pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()

        df["ts_code"] = ts_code
        df["symbol"] = ts_code_to_symbol(ts_code)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values("trade_date").reset_index(drop=True)

        # pct_chg 缺失时由 close 计算
        if "pct_chg" not in df.columns and "close" in df.columns:
            df["pct_chg"] = df["close"].pct_change() * 100

        for c in ["open", "close", "high", "low", "vol", "amount",
                  "amplitude", "pct_chg", "change", "turnover_rate"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        self._save_cache(cache_name, df)

        # 写回 DB（不影响返回，失败 silent）
        try:
            from data.db import get_conn, upsert_daily
            with get_conn() as conn:
                upsert_daily(conn, df, adjust=adjust)
        except Exception:
            pass

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
