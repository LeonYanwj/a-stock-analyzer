"""通过 vn.py gateway 下载历史 K 线并保存到 AlphaLab。"""
from datetime import datetime
from pathlib import Path
from typing import Any

from .gateway import _normalize_vt_symbol


def sync_history(runtime: Any, vt_symbols: list[str], start: datetime, end: datetime,
                 lab_path: str = "data/vnpy_lab", interval: Any = None) -> dict:
    """从已连接的 vn.py gateway 查询历史数据并写入项目内 AlphaLab。

    ``lab_path`` 必须位于当前项目目录下，避免 API 请求把生成物写到项目外。
    """
    if not runtime.connected or runtime.main_engine is None:
        raise RuntimeError("vn.py 尚未连接，请先连接迅投行情")

    root = Path.cwd().resolve()
    target = (root / lab_path).resolve()
    if target == root or root not in target.parents:
        raise RuntimeError("lab_path 必须位于项目目录内")

    try:
        from vnpy.alpha import AlphaLab
        from vnpy.trader.constant import Exchange, Interval
        from vnpy.trader.object import HistoryRequest
    except ImportError as exc:
        raise RuntimeError(f"vn.py Alpha 数据依赖未安装: {exc}") from exc

    target.mkdir(parents=True, exist_ok=True)
    lab = AlphaLab(str(target))
    bar_interval = interval or Interval.DAILY
    saved = {}
    for raw_symbol in sorted(set(vt_symbols)):
        vt_symbol = _normalize_vt_symbol(raw_symbol)
        symbol, exchange_name = vt_symbol.rsplit(".", 1)
        request = HistoryRequest(
            symbol=symbol,
            exchange=Exchange(exchange_name),
            start=start,
            end=end,
            interval=bar_interval,
        )
        bars = runtime.main_engine.query_history(request, "XT")
        if bars:
            lab.save_bar_data(bars)
        saved[vt_symbol] = len(bars)
    return {"lab_path": str(target), "interval": bar_interval.value, "saved": saved}
