# A 股股票分析与量化回测系统

基于 Tushare 数据源的 A 股本地量化回测脚手架：拉取行情 → 计算技术指标 → 跑策略 → 回测 → 出图。

## 安装

```bash
pip install -r requirements.txt
```

### vn.py 行情、策略与回测

项目新主链路通过 `astock.vnpy_runtime` 接入 vn.py 和迅投研 XtGateway。先安装
`requirements.txt` 中的 vn.py 依赖，并在项目根目录 `config.py` 中设置迅投 Token（该文件已被 Git 忽略）：

如果只是先验证 Linux 环境和 vn.py 链路，不需要 Token，直接运行本地模拟网关：

```bash
.venv-vnpy/bin/python -m examples.vnpy_linux_demo
```

看到连续的 `TICK 000001.SZSE` 和最后的 `OK` 即表示 vn.py 事件引擎、网关和订阅链路正常。这个 demo 只生成模拟行情，不代表 A 股真实行情。

第一里程碑验收优先运行下面的真实选股 Demo。它会只读连接 `config.py` 配置的 MySQL，
读取 `market_daily` 中的真实 A 股日线，通过生产信号链调用 vn.py Alpha101 并输出候选排名。
它不需要迅投 Token 或券商账号，不写数据库，也不会创建计划或下单。

```bash
.venv-vnpy/bin/python -m examples.vnpy_milestone_demo
```

最终看到 `MILESTONE DEMO OK` 表示已经使用真实行情跑通 vn.py Alpha101 选股。
若只需验证事件引擎和模拟网关，继续使用前面的 `examples.vnpy_linux_demo`。

```python
XTPY_MODE = "token"
XT_TOKEN = "你的迅投接口Token"
XT_ACCOUNT_ID = ""
XT_ACCOUNT_TYPE = "股票"
XT_PATH = ""
```

然后启动服务：

```bash
python -m uvicorn astock.api.main:app --host 0.0.0.0 --port 8000
```

常用接口：

- `GET /api/vnpy/status`：检查依赖和连接状态，不返回 Token；
- `POST /api/vnpy/connect`：连接迅投行情；
- `POST /api/vnpy/subscribe`：订阅 `000001.SZSE`、`600000.SSE` 等标的；
- `GET /api/vnpy/ticks`：读取最近收到的 Tick；
- `POST /api/vnpy/history/sync`：下载历史 K 线到 `data/vnpy_lab`；
- `POST /api/vnpy/backtests`：使用 AlphaLab 信号和 vn.py 官方
  `EquityDemoStrategy` 异步回测。

迅投 `xtquant` 不随 vn.py 主仓库发布，需要按 [vnpy_xt 官方说明](https://github.com/vnpy/vnpy_xt)
安装其 Python 库。新建交易实例和市场扫描固定使用 vn.py `Alpha101` 信号；仓库中的
`astock/vnpy_runtime/vnpy/` 目录仅用于源码审阅，不作为运行时安装来源。旧选股、模拟盘自动调仓和自定义回测入口已停用，历史记录只读保留。

在 [Tushare](https://tushare.pro) 注册账号获取 token，填入 `config.py` 的 `TUSHARE_TOKEN`。

## 运行

```bash
python -m examples.main
```

默认对平安银行（`000001.SZ`）跑 MA5×MA20 均线交叉策略，从 2024-01-01 到今天。运行后生成：

- `kline_indicators.png` — K 线 + 均线/MACD/RSI
- `backtest_result.png` — 回测净值曲线
- 控制台输出绩效指标与交易明细

## 模块结构

全部业务代码已收进 `astock/`；测试、示例和维护脚本分别归入 `tests/`、`examples/` 和 `scripts/`。
以下命令均从项目根目录运行，使用 `python -m` 保证业务包和根目录配置可以正确导入。
旧根目录命令已替换为模块命令；启动前先切换到项目根目录。

| 用途 | 命令 |
|------|------|
| API 服务 | `python -m uvicorn astock.api.main:app --host 0.0.0.0 --port 8000` |
| vn.py Alpha 选股 | 通过 `POST /api/market-scans` 运行 |
| 单股评级 | `python -m astock.rate 002028` |
| 模拟盘查询 | `python -m astock.paper list` |
| vn.py Alpha 回测 | `POST /api/vnpy/backtests` |
| 单股均线回测示例（拉取行情并出图） | `python -m examples.main` |
| 离线回测（读取已有缓存） | `python -m examples.demo_backtest --code 000001.SZ` |
| 股票基础资料和估值初始化 | `python -m scripts.init_data --limit 100` |
| 财务数据初始化 | `python -m scripts.init_financial --limit 500` |
| 交易日历初始化 | `python -m scripts.init_trade_calendar` |
| 查看缓存迁移范围 | `python -m scripts.migrate_cache_to_db --dry-run` |
| 查询历史回测 | `python -m scripts.query_backtest` |
| 离线选股流程验证 | `python -m tests.test_mock` |
| 离线评级流程验证 | `python -m tests.test_rating` |
| 每日评级规则测试 | `python -m unittest tests.test_daily_rating_rules` |

数据初始化命令会请求行情源并写入配置的数据库；执行前需确认目标环境。
完整测试可使用 `python -m unittest discover -s tests -t .`；两个 Mock 流程仍按上表单独运行。
安装完整依赖需要 Python 3.10 或以上。API 启动命令已改为 `python -m uvicorn astock.api.main:app`。
行情缓存和运行输出仍使用项目根目录的 `cache/`、`output/`、`data/vnpy_lab/`，其中 `astock/data/` 仅存放 Python 源码。
项目进展见 [项目状态](docs/PROJECT_STATUS.md)，接口说明统一见 [API 文档](API.md)。API 文档顶部的“第一阶段已修改”标记列出了快速/全局扫描和默认规则策略的改动。

```text
a-stock-analyzer/
├── astock/              # 全部业务源码
│   ├── api/             # FastAPI 路由、认证、任务和调度
│   ├── data/            # 数据访问与行情获取代码
│   ├── trade_run/       # 交易实例、计划和账务
│   ├── vnpy_runtime/    # vn.py 适配层
│   ├── analysis/        # 技术指标
│   ├── factors/         # 因子计算
│   ├── strategy/        # 策略接口和均线策略
│   ├── backtest/        # 回测引擎与指标
│   ├── utils/           # 绘图工具
│   └── *.py             # 选股、评级、模拟盘和研究模块
├── examples/            # 回测和 vn.py 示例
├── scripts/             # 数据准备、迁移和查询命令
├── tests/               # 单元测试和离线验证
├── docs/                # API、项目状态和迁移说明
├── sql/                 # 数据库结构与版本化迁移
├── config.example.py    # 配置模板
├── config.py            # 本地配置（自行创建，Git 忽略）
├── requirements.txt     # 依赖
├── README.md            # 项目入口说明
├── AGENTS.md            # 本地协作约定
└── MEMORY.md            # 本地项目记忆
```

## 多因子选股

筛选全部沪深主板，剔除创业板、科创板、北交所、ST、次新股和财务高风险股票，
对合格股票生成每日评级快照后选 Top N：

```bash
python -m astock.screen --limit 50    # 试跑 50 只
python -m astock.screen               # 全部主板（~3000 只，首跑 25-40 分钟）
```

输出 `output/picks_YYYYMMDD.csv`。因子权重在 `astock/factors/compute.py` 顶部可调。

## 扩展策略

继承 `astock.strategy.base.Strategy`，实现 `generate_signals(df) -> pd.Series`，信号 `1` 买入、`-1` 卖出、`0` 持有。在 `examples/main.py` 替换策略实例即可。

## 回测约定

- 初始资金 4 万元
- 佣金万一，单笔最低 5 元（不免 5）
- 滑点 0.1%
- 信号触发当日按收盘价成交、全仓买卖
- 股数按 100 股（一手）取整

---

## API 后端（FastAPI）

把研究/选股/回测/模拟盘的全部能力暴露成 HTTP 接口。完整接口文档见 **[API 文档](docs/API.md)**。

### 启动

```bash
python -m uvicorn astock.api.main:app --host 0.0.0.0 --port 8000
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

每个接口的参数、返回示例、注意事项见 **[API 文档](docs/API.md)**，含：
- 所有 33 个接口的完整说明
- SSE 前端 JS EventSource 代码示例
- 异步任务前端轮询模式
- 错误码对照表
- VPS 部署 cheatsheet
