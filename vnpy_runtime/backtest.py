"""vn.py Alpha 回测入口。

这里不再复制旧回测逻辑，而是把参数和生命周期交给 vn.py 的
``vnpy.alpha.strategy.BacktestingEngine``。
"""
from datetime import datetime
from pathlib import Path
from typing import Any

from .gateway import _normalize_vt_symbol
from .strategy import load_equity_demo_strategy


def create_alpha_backtesting_engine(lab: Any, vt_symbols: list[str], start: datetime,
                                    end: datetime, capital: float = 1_000_000,
                                    interval: Any = None) -> Any:
    """创建已配置但尚未运行的 vn.py Alpha 回测引擎。"""
    try:
        from vnpy.alpha.strategy import BacktestingEngine
        from vnpy.trader.constant import Interval
    except ImportError as exc:
        raise RuntimeError(f"vn.py Alpha 回测依赖未安装: {exc}") from exc

    engine = BacktestingEngine(lab)
    engine.set_parameters(
        vt_symbols=vt_symbols,
        interval=interval or Interval.DAILY,
        start=start,
        end=end,
        capital=int(capital),
    )
    return engine


def run_alpha_backtest(engine: Any, strategy_class: type, setting: dict,
                       signal_df: Any) -> dict:
    """运行 vn.py Alpha 回测并返回统计结果。"""
    engine.add_strategy(strategy_class, setting, signal_df)
    engine.load_data()
    engine.run_backtesting()
    daily = engine.calculate_result()
    stats = engine.calculate_statistics()
    return {
        "statistics": stats,
        "daily_results": daily,
        "trades": engine.get_all_trades(),
        "orders": engine.get_all_orders(),
    }


def run_named_alpha_backtest(vt_symbols: list[str], start: datetime, end: datetime,
                             signal_name: str, lab_path: str = "data/vnpy_lab",
                             capital: float = 1_000_000,
                             setting: dict | None = None) -> dict:
    """读取 AlphaLab 中已生成的模型信号，运行官方股票策略回测。

    信号必须是 vn.py 约定的 ``datetime/vt_symbol/signal`` 三列；模型训练和信号
    生成仍由 vn.py Alpha 投研流程负责，本项目不再维护另一套打分算法。
    """
    root = Path.cwd().resolve()
    target = (root / lab_path).resolve()
    if target == root or root not in target.parents:
        raise RuntimeError("lab_path 必须位于项目目录内")
    if not signal_name or Path(signal_name).name != signal_name:
        raise RuntimeError("signal_name 只能是信号文件名，不允许包含路径")

    try:
        from vnpy.alpha import AlphaLab
    except ImportError as exc:
        raise RuntimeError(f"vn.py Alpha 回测依赖未安装: {exc}") from exc

    symbols = sorted({_normalize_vt_symbol(item) for item in vt_symbols})
    if not symbols:
        raise RuntimeError("至少需要一个回测标的")
    lab = AlphaLab(str(target))
    signal = lab.load_signal(signal_name)
    if signal is None:
        raise RuntimeError(f"找不到信号文件：{signal_name}")
    required = {"datetime", "vt_symbol", "signal"}
    missing = required.difference(signal.columns)
    if missing:
        raise RuntimeError(f"信号文件缺少字段：{', '.join(sorted(missing))}")
    signal = signal.filter(signal["vt_symbol"].is_in(symbols))
    if signal.is_empty():
        raise RuntimeError("信号文件中没有指定标的的数据")

    engine = create_alpha_backtesting_engine(lab, symbols, start, end, capital)
    result = run_alpha_backtest(
        engine, load_equity_demo_strategy(), setting or {}, signal
    )
    daily_rows = result["daily_results"].to_dicts() if result["daily_results"] is not None else []
    return {
        "strategy": "EquityDemoStrategy",
        "signal_name": signal_name,
        "vt_symbols": symbols,
        "statistics": _json_safe(result["statistics"]),
        "daily_results": _json_safe(daily_rows),
        "trade_count": len(result["trades"]),
        "order_count": len(result["orders"]),
    }


def _json_safe(value: Any) -> Any:
    """递归转换日期、numpy 标量和 Polars 值，供任务 API 返回。"""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except Exception:
            pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
