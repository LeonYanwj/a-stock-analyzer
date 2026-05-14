# A 股股票分析与量化回测系统

基于 Tushare 数据源的 A 股本地量化回测脚手架：拉取行情 → 计算技术指标 → 跑策略 → 回测 → 出图。

## 安装

```bash
pip install -r requirements.txt
```

在 [Tushare](https://tushare.pro) 注册账号获取 token，填入 `config.py` 的 `TUSHARE_TOKEN`。

## 运行

```bash
python main.py
```

默认对平安银行（`000001.SZ`）跑 MA5×MA20 均线交叉策略，从 2024-01-01 到今天。运行后生成：

- `kline_indicators.png` — K 线 + 均线/MACD/RSI
- `backtest_result.png` — 回测净值曲线
- 控制台输出绩效指标与交易明细

## 模块结构

```
a-stock-analyzer/
├── main.py              # 入口，串联完整流程
├── config.py            # Tushare token、回测参数
├── data/
│   └── fetcher.py       # 行情数据获取 + 本地 CSV 缓存
├── analysis/
│   └── indicators.py    # MA / EMA / MACD / RSI / KDJ / BOLL
├── strategy/
│   ├── base.py          # 策略抽象基类
│   └── ma_cross.py      # 均线交叉策略
├── backtest/
│   ├── engine.py        # 回测引擎（手续费、滑点、全仓买卖）
│   └── metrics.py       # 收益率、回撤、夏普、胜率
└── utils/
    └── plot.py          # 蜡烛图、净值曲线
```

## 扩展策略

继承 `strategy.base.Strategy`，实现 `generate_signals(df) -> pd.Series`，信号 `1` 买入、`-1` 卖出、`0` 持有。在 `main.py` 替换策略实例即可。

## 回测约定

- 初始资金 10 万元
- 手续费万三、滑点 0.1%
- 信号触发当日按收盘价成交、全仓买卖
- 股数按 100 股（一手）取整
