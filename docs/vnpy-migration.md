# vn.py 主链路迁移

## 当前状态

项目已加入 `vnpy[alpha]` 和 `vnpy_xt` 依赖，并提供了惰性加载的运行时适配层。

## Linux 快速演示

不配置券商账号时，可运行本地模拟网关验证 vn.py 的事件和行情链路：

```bash
.venv-vnpy/bin/python -m examples.vnpy_linux_demo
```

该 demo 使用项目内的 `LocalDemoGateway`，会生成递增的模拟 Tick，不会连接外部服务，也不会下单。需要测试真实期货柜台时再使用官方 `vnpy_ctptest`；A 股实盘仍需另接支持 Linux 的券商接口。

第一里程碑的完整验收 Demo 默认只读检查项目 MySQL 的 `market_daily`；行情不足时对
当前截面从 AKShare/东方财富按需获取真实日线，再调用 vn.py Alpha101，并输出数据截面、
实际有效股票数量及候选排名：

```bash
.venv-vnpy/bin/python -m examples.vnpy_milestone_demo
```

可用 `--data-source mysql` 禁止回退，或用 `--data-source akshare --quick-limit 50` 跳过 MySQL。
AKShare 路径不写业务数据、不会下单，也不读取迅投 Token；历史 `--as-of` 不自动回退到
今天的股票池，`full` 全市场扫描需先批量准备 MySQL 行情。
最终输出 `MILESTONE DEMO OK` 才表示真实行情选股链路已通过；前面的模拟 Tick Demo
只用于排查 vn.py 事件引擎和网关环境。
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

策略入口为 `astock.vnpy_runtime.strategy.load_equity_demo_strategy()`，对应 vn.py 官方
`EquityDemoStrategy`。交易实例候选由
`astock.vnpy_runtime.signals.generate_alpha101_signals()` 计算：它使用已安装的 vn.py
`Alpha101` 表达式生成无未来数据的横截面信号，信号格式与 `EquityDemoStrategy` 一致；
本项目的交易计划层继续负责限仓、整手、人工报价确认和成交审计。
回测入口为 `astock.vnpy_runtime.backtest`，直接调用 `vnpy.alpha.strategy.BacktestingEngine`。

## 只读选股 Demo

要直接查看 vn.py 根据 Alpha101 排出的候选股票，先在 Python 3.10+ 环境安装依赖：

```bash
python -m venv .venv-vnpy
.venv-vnpy/bin/pip install -r requirements.txt
```

然后运行：

```bash
.venv-vnpy/bin/python -m examples.vnpy_stock_picker \
  --strategy medium_term --asset stock --limit 10
```

该命令从 `config.py` 配置的 MySQL 读取 `market_daily`，默认只使用决策日前的复权日线，
输出排名、代码、参考价、Alpha 信号分数和有效因子数。可选策略为
`short_term`、`medium_term`、`long_term`，也可用 `--as-of 2026-09-08` 固定历史截面。
程序是只读的，不创建交易实例、交易计划，不连接券商，也不会下单。

没有 MySQL 时，可用 CSV 离线验证。CSV 至少包含
`ts_code,trade_date,open,high,low,close`，成交量列使用 `volume` 或 `vol`：

```bash
.venv-vnpy/bin/python -m examples.vnpy_stock_picker \
  --csv data/stocks.csv --strategy short_term --limit 5
```

CSV 需要至少 3 只标的，并为每只标的准备足够历史日线（当前 Alpha101 最长窗口为 60 个交易日，
建议准备 120～320 个交易日）。

第一阶段市场扫描提供三个默认规则：`trend_momentum`（趋势动量）、
`breakout_volume`（突破放量）和 `low_volatility`（低波动趋势）。扫描支持
`stock_scope=quick`（先按最新成交额缩小股票池）和 `stock_scope=full`（全部合格主板），
两种模式都只返回候选，不操作账户或仓位。

仓库内的 `astock/vnpy_runtime/vnpy/` 是随项目保存的源码审阅副本，便于核对 Alpha101
和官方策略实现；部署服务器不需要提交或安装这份副本。

## 服务器配置

不要把 Token 写入接口请求体、日志或提交记录。若使用迅投客户端模式，改用
`XTPY_MODE="client"` 和 `XT_PATH`，并保持客户端运行。

## 迁移顺序

1. 安装依赖并验证 `/api/vnpy/status`；
2. 连接迅投并订阅一只股票/ETF，确认 Tick/K 线事件；
3. 新建交易实例和独立市场扫描固定使用 vn.py Alpha101 信号；
4. 通过 vn.py 回测和仿真验证策略表现；
5. 旧 `legacy/new` 实例和数据库表只读保留，不清理历史数据。
