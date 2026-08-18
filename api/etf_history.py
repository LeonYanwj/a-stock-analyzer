"""ETF 历史日线同步的纯业务逻辑。

路由层负责鉴权和数据库中的 ETF 主数据；本模块只负责规范化输入日期、
提交给数据抓取器，并通过既有任务系统报告进度，方便在不接触 MySQL 或
AKShare 的测试环境中验证。
"""
import re
from datetime import date, timedelta
from typing import Callable, Optional, Tuple


_ETF_CODE_RE = re.compile(r"^\d{6}\.(?:SH|SZ)$")


def normalize_etf_code(ts_code: str) -> str:
    """返回标准 ETF 代码，例如 ``510300.SH``。

    AKShare 的 ETF 日线接口只接受六位证券代码对应的沪深 ETF；代码格式不
    正确时在进入后台任务前拒绝，避免产生一个必然失败的异步任务。
    """
    normalized = str(ts_code or "").strip().upper()
    if not _ETF_CODE_RE.fullmatch(normalized):
        raise ValueError("ETF 代码必须是 6 位代码加 .SH 或 .SZ，例如 510300.SH")
    return normalized


def build_history_window(start_date: Optional[date] = None,
                         end_date: Optional[date] = None,
                         today: Optional[date] = None) -> Tuple[date, date]:
    """构造历史同步区间；默认回补最近 90 个自然日。"""
    default_end = today or date.today()
    end = end_date or default_end
    start = start_date or (end - timedelta(days=90))
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")
    return start, end


def run_etf_history_sync(task, ts_code: str, start_date: date, end_date: date,
                         fetcher_factory: Optional[Callable] = None) -> dict:
    """运行单只 ETF 日线同步；``DataFetcher`` 会将日线 UPSERT 到 MySQL。"""
    task.report(5, "开始拉取 ETF 历史日线")
    if fetcher_factory is None:
        # 延迟导入：接口的管理和状态查询不应因 AKShare 缺失而无法加载。
        from data.fetcher import DataFetcher
        fetcher_factory = DataFetcher
    df = fetcher_factory().get_etf_daily(
        ts_code,
        start_date.strftime("%Y%m%d"),
        end_date.strftime("%Y%m%d"),
    )
    fetched_rows = 0 if df is None else len(df)
    task.report(95, f"ETF 历史日线拉取完成：{fetched_rows} 条")
    return {
        "ts_code": ts_code,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "fetched_rows": fetched_rows,
    }
