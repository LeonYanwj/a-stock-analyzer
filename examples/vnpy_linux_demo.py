"""Linux 本地快速 demo：启动 vn.py，接收 10 个模拟 Tick 后退出。"""
from __future__ import annotations

from time import sleep

from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.event import EVENT_TICK
from vnpy.trader.object import SubscribeRequest
from vnpy.trader.constant import Exchange

from astock.vnpy_runtime.demo import LocalDemoGateway


def main() -> None:
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    main_engine.add_gateway(LocalDemoGateway)

    received = []

    def on_tick(event) -> None:
        tick = event.data
        received.append(tick)
        print(f"TICK {tick.vt_symbol} last={tick.last_price:.2f} volume={tick.volume}")

    event_engine.register(EVENT_TICK, on_tick)
    main_engine.connect({"间隔秒数": 0.1}, "LOCALDEMO")
    main_engine.subscribe(
        SubscribeRequest(symbol="000001", exchange=Exchange.SZSE), "LOCALDEMO"
    )

    for _ in range(30):
        if len(received) >= 10:
            break
        sleep(0.1)

    main_engine.close()
    if len(received) < 10:
        raise RuntimeError(f"demo 未收到足够 Tick：{len(received)}")
    print(f"OK: vn.py 本地网关收到 {len(received)} 个 Tick")


if __name__ == "__main__":
    main()
