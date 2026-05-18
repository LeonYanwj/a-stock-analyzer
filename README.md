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
├── main.py              # 单股回测入口（MA 交叉策略）
├── screen.py            # 全市场多因子选股入口
├── test_mock.py         # mock 数据测试，验证选股流程
├── config.py            # 配置（被 .gitignore 忽略，复制 config.example.py 使用）
├── universe.py          # 沪深主板股票池筛选
├── selector.py          # 因子打分与排序
├── data/
│   └── fetcher.py       # 行情数据获取（AKShare 后端）+ CSV 缓存
├── analysis/
│   └── indicators.py    # MA / EMA / MACD / RSI / KDJ / BOLL
├── strategy/
│   ├── base.py          # 策略抽象基类
│   └── ma_cross.py      # 均线交叉策略
├── factors/
│   └── compute.py       # 价值/动量/反转/规模/低波/流动性 7 因子
├── backtest/
│   ├── engine.py        # 回测引擎（手续费、滑点、全仓买卖）
│   └── metrics.py       # 收益率、回撤、夏普、胜率
└── utils/
    └── plot.py          # 蜡烛图、净值曲线
```

## 多因子选股

筛选沪深主板（剔除创业板/科创板/北交所/ST），打分 7 个因子选 Top N：

```bash
python screen.py --limit 50    # 试跑 50 只
python screen.py               # 全部主板（~3000 只，首跑 25-40 分钟）
```

输出 `output/picks_YYYYMMDD.csv`。因子权重在 `factors/compute.py` 顶部可调。

## 扩展策略

继承 `strategy.base.Strategy`，实现 `generate_signals(df) -> pd.Series`，信号 `1` 买入、`-1` 卖出、`0` 持有。在 `main.py` 替换策略实例即可。

## 回测约定

- 初始资金 4 万元
- 佣金万一，单笔最低 5 元（不免 5）
- 滑点 0.1%
- 信号触发当日按收盘价成交、全仓买卖
- 股数按 100 股（一手）取整
