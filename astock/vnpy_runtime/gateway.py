"""vn.py EventEngine + 迅投 gateway 的生命周期适配。

该模块使用惰性导入，使没有安装 vn.py 的开发/测试环境仍可加载 FastAPI。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from .config import VnpySettings


@dataclass
class VnpyRuntime:
    """管理 vn.py 主引擎和 XtGateway；Token 永不进入状态返回值。"""

    settings: VnpySettings = field(default_factory=VnpySettings.from_config)
    event_engine: Any = field(default=None, init=False)
    main_engine: Any = field(default=None, init=False)
    connected: bool = field(default=False, init=False)
    subscriptions: set[str] = field(default_factory=set, init=False)
    latest_ticks: dict[str, dict] = field(default_factory=dict, init=False)
    last_error: str | None = field(default=None, init=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def status(self) -> dict:
        """返回可直接用于监控页面的状态摘要。"""
        with self._lock:
            return {
                "available": self._imports_available(),
                "connected": self.connected,
                "subscriptions": sorted(self.subscriptions),
                "latest_ticks": sorted(self.latest_ticks),
                "settings": self.settings.public_dict(),
                "last_error": self.last_error,
            }

    def connect(self) -> dict:
        """启动 vn.py 主引擎并连接 XtGateway。"""
        with self._lock:
            if self.connected:
                return self.status()
            if not self.settings.configured:
                raise RuntimeError(
                    "迅投未配置：请在 config.py 设置 XT_TOKEN；客户端模式还需设置 XT_PATH"
                )
            try:
                from vnpy.event import EventEngine
                from vnpy.trader.engine import MainEngine
                from vnpy.trader.event import EVENT_TICK
                from vnpy_xt import XtGateway
            except ImportError as exc:
                # vnpy_xt 可被 pip 安装，但其 xtquant 底层扩展仍需与操作系统
                # 匹配；例如 Linux 环境拿到 Windows .pyd 时同样会在这里失败。
                self.last_error = f"vn.py/迅投运行库导入失败（请检查安装包与操作系统是否匹配）: {exc}"
                raise RuntimeError(self.last_error) from exc

            self.event_engine = EventEngine()
            self.event_engine.register(EVENT_TICK, self._on_tick)
            self.main_engine = MainEngine(self.event_engine)
            self.main_engine.add_gateway(XtGateway)
            setting = {
                "token": self.settings.xt_token or "",
                "股票市场": "是",
                "期货市场": "否",
                "期权市场": "否",
                "仿真交易": "否",
                "账号类型": self.settings.xt_account_type,
                "资金账号": self.settings.xt_account_id or "",
                "QMT路径": self.settings.xt_path or "",
            }
            # XtGateway 的 connect 只接收其配置字典；Token 保持在进程内。
            self.main_engine.connect(setting, "XT")
            self.connected = True
            self.last_error = None
            return self.status()

    def disconnect(self) -> dict:
        """断开 gateway 并释放主引擎。"""
        with self._lock:
            if self.main_engine is not None:
                self.main_engine.close()
            self.main_engine = None
            self.event_engine = None
            self.connected = False
            self.subscriptions.clear()
            self.latest_ticks.clear()
            return self.status()

    def ticks(self, vt_symbols: list[str] | None = None) -> list[dict]:
        """返回最近收到的 Tick 快照；没有 Tick 时返回空列表。"""
        with self._lock:
            symbols = {_normalize_vt_symbol(item) for item in vt_symbols or []}
            values = self.latest_ticks.values()
            if symbols:
                values = [item for item in values if item["vt_symbol"] in symbols]
            return [dict(item) for item in values]

    def subscribe(self, vt_symbols: list[str]) -> dict:
        """订阅股票/ETF，代码格式使用 vn.py 的 ``symbol.exchange``。"""
        with self._lock:
            if not self.connected or self.main_engine is None:
                raise RuntimeError("vn.py 尚未连接，请先连接迅投行情")
            from vnpy.trader.object import SubscribeRequest
            from vnpy.trader.utility import extract_vt_symbol

            gateway = self.main_engine.get_gateway("XT")
            for vt_symbol in sorted(set(vt_symbols)):
                normalized = _normalize_vt_symbol(vt_symbol)
                symbol, exchange = extract_vt_symbol(normalized)
                gateway.subscribe(SubscribeRequest(symbol=symbol, exchange=exchange))
                self.subscriptions.add(normalized)
            return self.status()

    @staticmethod
    def _imports_available() -> bool:
        try:
            import vnpy  # noqa: F401
            import vnpy_xt  # noqa: F401
        except ImportError:
            return False
        return True

    def _on_tick(self, event: Any) -> None:
        """把 vn.py TickData 转换为安全的 JSON 快照。"""
        tick = event.data
        if tick is None:
            return
        snapshot = {
            "vt_symbol": tick.vt_symbol,
            "symbol": tick.symbol,
            "exchange": tick.exchange.value,
            "datetime": tick.datetime.isoformat() if tick.datetime else None,
            "last_price": float(tick.last_price or 0),
            "volume": float(tick.volume or 0),
            "turnover": float(tick.turnover or 0),
            "open_price": float(tick.open_price or 0),
            "high_price": float(tick.high_price or 0),
            "low_price": float(tick.low_price or 0),
            "pre_close": float(tick.pre_close or 0),
            "bid_price_1": float(tick.bid_price_1 or 0),
            "ask_price_1": float(tick.ask_price_1 or 0),
            "bid_volume_1": float(tick.bid_volume_1 or 0),
            "ask_volume_1": float(tick.ask_volume_1 or 0),
        }
        with self._lock:
            self.latest_ticks[tick.vt_symbol] = snapshot


def _normalize_vt_symbol(vt_symbol: str) -> str:
    """兼容项目旧的 ``.SH/.SZ`` 代码，转换为 vn.py 的交易所枚举值。"""
    raw = str(vt_symbol or "").strip().upper()
    if "." not in raw:
        raise ValueError(f"证券代码必须带交易所后缀：{vt_symbol}")
    symbol, exchange = raw.rsplit(".", 1)
    exchange = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}.get(exchange, exchange)
    return f"{symbol}.{exchange}"
