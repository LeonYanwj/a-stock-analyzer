# vn.py 主链路迁移

## 当前状态

项目已加入 `vnpy[alpha]` 和 `vnpy_xt` 依赖，并提供了惰性加载的运行时适配层。

## Linux 快速演示

不配置券商账号时，可运行本地模拟网关验证 vn.py 的事件和行情链路：

```bash
.venv-vnpy/bin/python examples/vnpy_linux_demo.py
```

该 demo 使用项目内的 `LocalDemoGateway`，会生成递增的模拟 Tick，不会连接外部服务，也不会下单。需要测试真实期货柜台时再使用官方 `vnpy_ctptest`；A 股实盘仍需另接支持 Linux 的券商接口。
迅投配置统一放在项目根目录的 `config.py`（该文件已被 Git 忽略）：

```python
XTPY_MODE = "token"
XT_TOKEN = "你的迅投接口Token"
XT_ACCOUNT_ID = ""
XT_ACCOUNT_TYPE = "股票"
XT_PATH = ""
```

适配层：

- `GET /api/vnpy/status`：查看 vn.py/迅投依赖和连接状态（不返回 Token）
- `GET /api/vnpy/strategies`：查看当前接入的 vn.py 股票策略
- `POST /api/vnpy/connect`：从 `config.py` 的 `XT_TOKEN` 连接迅投行情
- `POST /api/vnpy/subscribe`：订阅 vn.py 格式的股票/ETF，例如 `000001.SZSE`、`510300.SSE`
  （接口同时兼容项目旧格式 `000001.SZ`、`600000.SH`）
- `GET /api/vnpy/ticks?symbols=000001.SZSE`：读取最近收到的 Tick 快照
- `POST /api/vnpy/history/sync`：通过 XtGateway 下载历史 K 线到项目内 `data/vnpy_lab`
- `POST /api/vnpy/backtests`：读取 AlphaLab 的模型信号，调用 vn.py `BacktestingEngine`
  和官方 `EquityDemoStrategy` 异步回测；结果通过 `/api/tasks/{task_id}` 查询
- `POST /api/vnpy/disconnect`：断开行情 gateway

策略入口为 `vnpy_runtime.strategy.load_equity_demo_strategy()`，对应 vn.py 官方
`EquityDemoStrategy`。回测入口为 `vnpy_runtime.backtest`，直接调用
`vnpy.alpha.strategy.BacktestingEngine`。

## 服务器配置

不要把 Token 写入接口请求体、日志或提交记录。若使用迅投客户端模式，改用
`XTPY_MODE="client"` 和 `XT_PATH`，并保持客户端运行。

## 迁移顺序

1. 安装依赖并验证 `/api/vnpy/status`；
2. 连接迅投并订阅一只股票/ETF，确认 Tick/K 线事件；
3. 将 vn.py Alpha 信号和回测结果接入前端；
4. 新链路完成回测和仿真验证后，再下线旧 `screen`、`paper` 和自定义回测链路；
5. 旧数据库表只读保留，不清理历史数据。
