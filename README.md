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

筛选全部沪深主板，剔除创业板、科创板、北交所、ST、次新股和财务高风险股票，
对合格股票生成每日评级快照后选 Top N：

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

---

## API 后端（FastAPI）

把研究/选股/回测/模拟盘的全部能力暴露成 HTTP 接口。完整接口文档见 **[API.md](API.md)**。

### 启动

```bash
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
# 交互式文档：http://localhost:8000/docs
```

### 接口分布（共 50 个）

| 域 | 路径前缀 | 数量 | 主要能力 |
|---|---|:---:|---|
| 账户/模拟盘 | `/api/accounts` | 13 | 模拟盘生命周期（**新建→运行→终止→归档**）、持仓（**含 SSE 流式**）、成交、净值、复盘、自动调仓、每日运行 |
| 选股 | `/api/screen` | 4 | 同步 / 异步选股、策略列表、持仓数计算 |
| 市场扫描 | `/api/market-scans` | 3 | 独立的后台候选池扫描、任务记录与实时进度；不依赖交易实例，也不改动账务 |
| 评级 | `/api/rate` | 2 | 单股 5 维度评级（同步 + **SSE 流式**）|
| 回测 | `/api/backtest` | 3 | 历史回测查询、异步触发新回测 |
| 股票数据 | `/api/stocks` | 5 | 股票/日线/估值/财务查询 |
| 任务管理 | `/api/tasks` | 4 | 异步任务状态、DB 归档历史查询 |
| 实盘持仓 | `/api/holdings` | 5 | 实盘持仓 CRUD、盘后分析和邮件 |
| 通知 | `/api/notify` | 3 | SMTP 配置、测试邮件 |
| 自选股 | `/api/watchlist` | 5 | 自选股 CRUD、异步生成每日汇总邮件 |
| 系统 | `/` `/health` | 2 | 健康检查 |

每日 18:00 生成全市场评级并复查模拟盘。财务高风险、ST、退市风险和 D 级持仓立即退出；
普通趋势转弱连续两个评级日后退出。新候选必须至少为 B 级且比分数被替换股票高 0.20，
否则保留现金。自选股在交易日 19:00 汇总评级变化、行情和消息并发送邮件。

### 接口设计规则

**涉及外网调用（AKShare/Tushare 等）的接口必须提供 SSE 流式版本**，仅查 MySQL 的接口保持同步。原因：外网慢且不可控（5-40秒），前端需要看到进度。当前 SSE 接口：
- `GET /api/rate/{code}/stream` - 单股评级（8 阶段）
- `GET /api/accounts/{id}/positions/stream` - 持仓查询（5 阶段）

分钟级长任务（选股/回测/调仓/每日运行）用异步任务模式（`/async` + 任务队列 + DB 归档），不用 SSE。

### 三种调用模式

| 模式 | 适用 | 例子 |
|---|---|---|
| **同步**（`GET`，一次性返回）| 快速查询（<1 秒）| `GET /api/accounts/1/positions` |
| **异步任务**（POST 提交 → 轮询）| 慢任务（10秒+）| `POST /api/screen/async` → `GET /api/tasks/{id}` |
| **SSE 流式**（一个连接持续推进度）| 中等耗时 + 需要实时反馈 | `GET /api/rate/{code}/stream` |

### 快速上手

```bash
# 同步：查持仓（含股票名 + 实时价 + 价格来源）
curl "http://localhost:8000/api/accounts/1/positions?use_realtime=true"

# 异步：跑全市场选股（不会卡浏览器）
curl -X POST "http://localhost:8000/api/screen/async?strategy=swing&capital=100000"
# → {"task_id": "xxx"}
curl "http://localhost:8000/api/tasks/xxx"
# → {"status": "done", "result": {...}}

# SSE 流式：单股评级（边算边看进度）
curl -N "http://localhost:8000/api/rate/600519/stream?strategy=swing"
# → data: {"progress": 30, "msg": "计算量价因子..."}
# → data: {"progress": 100, "result": {...}}

# 异步任务历史（重启不丢，DB 归档）
curl "http://localhost:8000/api/tasks/history?name=backtest&limit=10"
```

### 错误响应统一格式

所有错误（4xx/5xx）返回结构化 JSON：

```json
{
  "error":   "ACCOUNT_NOT_FOUND",
  "message": "账户 99999 不存在",
  "detail":  "..."
}
```

### 详细接口文档

每个接口的参数、返回示例、注意事项见 **[API.md](API.md)**，含：
- 所有 33 个接口的完整说明
- SSE 前端 JS EventSource 代码示例
- 异步任务前端轮询模式
- 错误码对照表
- VPS 部署 cheatsheet
