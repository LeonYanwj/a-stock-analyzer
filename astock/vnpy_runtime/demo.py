"""无需券商账号的 Linux 本地演示网关。

用于先验证 vn.py 的 EventEngine、MainEngine、Gateway 和 Tick 事件链路。
它只生成模拟行情，不连接外部服务，也不会产生真实订单。
"""
from __future__ import annotations

from datetime import datetime
from threading import Event as ThreadEvent, Lock, Thread
from time import sleep

from vnpy.event import EventEngine
from vnpy.trader.constant import Exchange
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import SubscribeRequest, TickData


class LocalDemoGateway(BaseGateway):
    """生成递增价格的本地模拟行情。"""

    default_name = "LOCALDEMO"
    default_setting = {"初始价格": 100.0, "间隔秒数": 0.2}
    exchanges = [Exchange.SSE, Exchange.SZSE]

    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        super().__init__(event_engine, gateway_name)
        self._symbols: set[tuple[str, Exchange]] = set()
        self._prices: dict[str, float] = {}
        self._stop = ThreadEvent()
        self._thread: Thread | None = None
        self._lock = Lock()
        self._interval = 0.2

    def connect(self, setting: dict) -> None:
        self._interval = float(setting.get("间隔秒数", 0.2))
        self._stop.clear()
        if not self._thread or not self._thread.is_alive():
            self._thread = Thread(target=self._run, name="local-demo-market", daemon=True)
            self._thread.start()
        self.write_log("LOCALDEMO 已连接（本地模拟行情）")

    def close(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)
        self._thread = None

    def subscribe(self, req: SubscribeRequest) -> None:
        with self._lock:
            self._symbols.add((req.symbol, req.exchange))
            self._prices.setdefault(req.vt_symbol, 100.0)
        self.write_log(f"已订阅 {req.vt_symbol}")

    def send_order(self, req):
        raise RuntimeError("LOCALDEMO 仅演示行情，不支持真实下单")

    def cancel_order(self, req) -> None:
        return None

    def query_account(self) -> None:
        return None

    def query_position(self) -> None:
        return None

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            with self._lock:
                symbols = list(self._symbols)
            for symbol, exchange in symbols:
                vt_symbol = f"{symbol}.{exchange.value}"
                price = self._prices.get(vt_symbol, 100.0) + 0.1
                self._prices[vt_symbol] = price
                tick = TickData(
                    symbol=symbol,
                    exchange=exchange,
                    datetime=datetime.now(),
                    gateway_name=self.gateway_name,
                    last_price=price,
                    volume=1,
                    bid_price_1=price - 0.1,
                    ask_price_1=price + 0.1,
                    bid_volume_1=100,
                    ask_volume_1=100,
                )
                self.on_tick(tick)
