# A 股量化系统 - 项目状态地图

> **Repository**: https://github.com/LeonYanwj/a-stock-analyzer
> 本文档是项目的"GPS"，每次重大变更后更新。

---

## 一、项目愿景

打造一个**个人级量化交易系统**，分 4 个阶段：

```
Tier 1: 研究平台      → 在历史数据上验证因子和策略
Tier 2: 数据基础      → MySQL 持久化，多源容错
Tier 3: 模拟盘        → 真实时间 × 虚拟资金跑策略
Tier 4: 实盘 / ML     → 接券商 API、机器学习增强
```

最终运行形态（每日）：

```
09:00  ├─ 自动拉昨日新数据入库
       ├─ 三个策略账户（短线/波段/趋势）并行选股
       └─ 给出 3 份选股清单 + Top 50 评级报告

15:00  ├─ 计算今日持仓盈亏
       ├─ 检查止损触发
       ├─ 生成复盘报告
       └─ 准备明日调仓清单（若是调仓日）

周末   ├─ 周度绩效 vs 沪深 300
       ├─ 因子表现分析
       └─ 策略权重微调建议
```

---

## 二、进度总览

| Tier | 模块 | 进度 |
|------|------|:----:|
| 1 | 量化研究平台 | ██████████ 100% |
| 2 | 数据基础设施 | ██████████ 100% |
| 3 | 模拟盘 | █████████░ 98% |
| 4 | 人工实盘验证闭环 | ██████░░░░ 60% |
| 5 | 自动券商下单 / ML | ██░░░░░░░░ 20% |
| 6 | REST API（旧接口 + 新交易实例） | ███████░░░ 70% |
| | 整体（按最终自动化实盘目标） | ███░░░░░░░ 30% |

**最新动态**：
- **交易实例人工验证版已进入可联调阶段**：新 `trade_run` 链路已具备主/影子信号来源、盘前/午间计划编排、延迟行情的券商报价确认、实际成交回填、影子重合镜像和数据库任务幂等。ETF 结构与白名单规则已加入迁移；ETF 行情抓取、前端联调和连续交易日验证尚未完成。自动券商下单仍为零，绝不可把旧 `paper_*` 模拟盘当作实盘能力。
- **全市场每日评级与模拟盘日评上线**：覆盖全部沪深主板，排除创业板/科创板/北交所、ST、次新股和财务高风险股票；评级写入 `stock_rating_daily`。模拟盘每天复查，硬风险/D 级立即退出，普通转弱连续两天退出；候选不够强时允许空仓。
- **自选股日报上线**：新增 `/api/watchlist` CRUD 和异步日报接口；每交易日 19:00 汇总评级变化、价格、新闻、公告、研报并复用 SMTP 配置发送邮件。
- **实盘持仓分析模块上线**（与模拟盘 paper_* 完全隔离）：前端录入真实持仓 → 每交易日 **18:30 自动**对每只持仓做全方位分析（5 维评级 + 当天新闻/公告/研报原文 + 价格/涨跌/浮盈）→ **邮件推送**。接口 `/api/holdings`(CRUD)、`/api/notify/config`(SMTP)，手动触发 `GET /api/holdings/analyze/stream`(SSE)。板块消息留作下期。
- **盘中实时监控上线**：短线策略交易时段每 10 分钟盯持仓，跌破 -8% 或跌破 MA5 即清仓（盘中只卖不买，买入放收盘）。
- **自动定时调度上线**：APScheduler 集成进 FastAPI，每交易日 18:00（北京时间）自动「更新行情 → daily_runner」，随 uvicorn 启动即生效，无需系统 cron。查询/手动触发：`/api/scheduler/status`、`POST /api/scheduler/run-now`。
- **调仓机制升级**：所有模拟盘账户每天按策略评级复查。财务高风险、ST/退市风险和 D 级立即退出；普通趋势连续两天转弱才退出，新候选不够强时允许空仓。
- FastAPI 后端：20+ 接口覆盖账户/选股/评级/回测/股票数据，端到端测试通过。
- 启动: `uvicorn api.main:app --host 0.0.0.0 --port 8000`；文档: `/docs` (Swagger UI)

---

## 三、已建成（可直接使用）

### 3.1 代码模块（21 个文件）

| 类别 | 文件 | 作用 |
|------|------|------|
| **数据层** | `data/fetcher.py` | AKShare 多源数据获取（新浪/东财/同花顺/巨潮）|
| | `data/db.py` | MySQL 访问层（连接 + UPSERT + 各表读写）|
| **策略/因子** | `factors/compute.py` | 14 个因子计算 |
| | `strategies.py` | 3 个策略 profile + 资金量自适应持仓数 |
| | `selector.py` | 横截面打分排序 |
| | `news_scorer.py` | 关键词消息面评分 |
| | `pattern_recognizer.py` | 4 个 K 线形态识别 |
| | `single_grader.py` | 单股 5 维度评级（rate.py 用）|
| | `grader.py` | 多维度评级（早期版本）|
| | `universe.py` | 沪深主板筛选 |
| | `financial_risk.py` | 财务暴雷、ST 和退市风险硬过滤 |
| | `rating_store.py` | 全市场每日评级快照与趋势状态 |
| **入口** | `screen.py` | 全市场选股 |
| | `rate.py` | 单股深度评级 |
| | `main.py` | 旧单股回测（保留兼容）|
| | `demo_backtest.py` | 离线回测演示 |
| | `query_backtest.py` | 历史回测查询工具 |
| | `paper_engine.py` | **模拟盘核心引擎（新）** |
| | `paper.py` | 模拟盘 CLI |
| | `daily_runner.py` | **每日运行器**（全市场评级、持仓日评、择优替换；`run_all` 供调度&CLI 共用）|
| | `api/scheduler.py` | **APScheduler 定时任务**（18:00 全市场评级+模拟盘 / 盘中10min监控 / 18:30 持仓分析 / 19:00 自选股邮件）|
| **自选股** | `watchlist.py` | 自选股 CRUD、分组和评级策略 |
| | `watchlist_analyzer.py` | 自选股评级变化、行情和消息汇总邮件 |
| **实盘持仓(新)** | `real_holding.py` | 实盘持仓 CRUD（与模拟盘隔离，表自动建）|
| | `holding_analyzer.py` | 盘后全方位分析（5维评级+新闻/公告/研报+涨跌 → HTML 报告）|
| | `notify.py` | SMTP 邮件（配置存 DB `notify_config`，前端录入）|
| | `api/routes/holdings.py` | 持仓 CRUD + 分析触发(SSE)|
| | `api/routes/notify.py` | SMTP 配置 + 测试邮件接口 |
| **回测/验证** | `backtest_simple.py` | 基础回测（IC + 止损 + DB 写入）|
| | `backtest_rolling.py` | 动态滚动调权重 |
| | `walk_forward.py` | 样本外验证 |
| | `multi_window.py` | 多窗口验证 |
| **初始化** | `init_data.py` | 数据库初始化 |
| | `migrate_cache_to_db.py` | CSV cache 迁移 |
| | `sql/schema.sql` | 17 张表 schema |
| **测试** | `test_mock.py`, `test_rating.py` | mock 数据验证 |

### 3.2 数据资产（MySQL `quant_data` @ 47.93.14.90）

| 表 | 行数 | 状态 |
|----|-----:|:----:|
| `market_daily` | 827,793 | ✅ 501 只 × 8 年 |
| `market_stock_basic` | 5,204 | ✅ 全市场 + ST 标记 |
| `market_valuation` | 3,741,349 | ✅ 2000 只 × PE/PB 历史 |
| `market_financial` | **157,196** | ✅ 2000 只 × 平均 80 期季度财务 |
| `market_fund_flow` | ✅ 累积中 | 每次 fetcher 调用自动入库 |
| `market_trade_calendar` | **8,797** | ✅ 1990-2026 全量 |
| `market_universe_snapshot` | 0 | ⏸ 暂跳过（需 list_date/ST 历史，限制大）|
| `strategy_config` | 3 | ✅ short_term / swing / trend |
| `backtest_run` 等 (4 张) | 累积中 | ✅ **每次 backtest_simple.py 自动写入** |
| `paper_*` (5 张) | ✅ 累积中 | **Phase 2 MVP 已上线** |

### 3.3 14 个因子（5 维度）

| 维度 | 因子 | screen.py | rate.py |
|------|------|:---:|:---:|
| 量价 | mom_30 / reversal_5 / low_vol / liquidity / macd_hist / macd_slope / lxsz / pattern_score | ✅ | ✅ |
| 价值 | ep_ttm / bp / small_size | ✅ | ✅ |
| 质量 | roe / gross_margin | ✅ **已生效** | ✅ |
| 资金 | main_inflow / inflow_ratio | ✅ | ✅ |
| 消息 | news_score | ✅（精筛 Top N）| ✅ |

### 3.4 已验证的实测结果

**500 只主板，双周调仓，-8% 止损：**

| 配置 | 2019-2021 年化 | 2022-2024 年化 | 平均年化 | vs 沪深 300 |
|------|:---:|:---:|:---:|:---:|
| swing 静态权重 | +17.84% | -9.46% | +4.19% | +5% |
| **动态滚动调权重** | +14.98% | **+1.11%** | **+8.05%** | **+10-15%** ⭐ |
| 沪深 300 | +18.58% | -7.46% | +5.56% | - |

**结论**：动态滚动调权重在 2/3 窗口跑赢，平均超额沪深 300 约 **8-12%/年**，回撤减半。

---

## 四、关键命令清单

### 日常使用
```bash
git pull
python screen.py --strategy swing --capital 100000  # 选股（10 万本金推荐 8 只）
python rate.py 002028 --strategy swing              # 单股评级
```

### 回测验证
```bash
python backtest_simple.py --months 6 --strategy swing --rebal-weeks 2 --ic
python backtest_rolling.py --start-year 2022 --end-year 2024
python walk_forward.py --end-date 20250517
python multi_window.py --start-year 2022 --end-year 2024

# 查询历史回测（DB 自动存档）
python query_backtest.py                       # 列出最近 20 次
python query_backtest.py --run 5               # 查看第 5 次详情
python query_backtest.py --compare 3 5 7       # 对比多次
```

### REST API（前后端分离）
```bash
# 启动后端
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000

# 访问
http://localhost:8000/docs                       # 交互式 API 文档
http://localhost:8000/health                     # 健康检查
http://localhost:8000/api/accounts               # 账户列表
http://localhost:8000/api/accounts/1/positions   # 持仓
http://localhost:8000/api/accounts/1/equity      # 净值曲线
http://localhost:8000/api/screen?strategy=swing&capital=100000   # 跑选股
http://localhost:8000/api/rate/002028?strategy=swing             # 单股评级
http://localhost:8000/api/backtest               # 历史回测
http://localhost:8000/api/stocks?search=平安      # 股票搜索
```

### 模拟盘（Phase 2）
```bash
# 创建账户
python paper.py create --name "swing-A" --capital 100000 --strategy swing
python paper.py list

# 自动调仓（一键完成：全市场评级 + 持仓日评 + 择优替换 + 快照）
python paper.py auto-rebalance --account 1 --limit 0

# 手动操作
python paper.py buy --account 1 --code 600487.SH --qty 200 --price 75
python paper.py stoploss --account 1
python paper.py snapshot --account 1

# 查询
python paper.py positions --account 1
python paper.py trades --account 1
python paper.py report --account 1

# ⭐ 每日运行器（CLI；正常由 APScheduler 自动跑，无需手动）
python daily_runner.py                        # 跑今天，所有活跃账户
python daily_runner.py --date 20260513        # 历史复盘
python daily_runner.py --account 1            # 只跑某个账户
python daily_runner.py --dry-run              # 预览不执行

# ⭐ 自动调度（APScheduler，随 uvicorn 启动，每交易日 18:00 北京时间自动跑）
curl http://localhost:8000/api/scheduler/status            # 查下次执行时间 / 上次结果
curl -X POST http://localhost:8000/api/scheduler/run-now   # 手动立即触发一次（补数据/验证）
```

### 数据初始化（一次性）
```bash
python migrate_cache_to_db.py                 # CSV cache → MySQL
python init_data.py --limit 2000              # 全市场 stock_basic + 估值
```

---

## 五、路线图

### 短期（完善 Tier 1/2）✅ **基本完成**
- [x] ~~接入财务数据 `market_financial`~~ ✅ fetcher DB 优先 + init_financial.py
- [x] ~~改造 backtest 写入 `backtest_*` 表~~ ✅ backtest_simple
- [x] ~~改造 fund_flow 写入 `market_fund_flow`~~ ✅ 每次调用自动入库
- [x] ~~初始化 `market_trade_calendar`~~ ✅ 8797 个交易日
- [x] ~~改进手续费模型（精确最低 5 元佣金）~~ ✅ calc_realistic_cost_rate
- [x] ~~`--capital` 推广到 4 个回测脚本~~ ✅
- [x] ~~把 DB 写入扩展到 backtest_rolling / walk_forward / multi_window~~ ✅ 已统一接入
- [ ] `market_universe_snapshot` 反推（需 list_date 数据，暂缓）

### 中期（Phase 2 模拟盘） ✅ **基本完成**
- [x] ~~模拟账户管理~~ ✅ paper_engine.py
- [x] ~~持仓跟踪 + 止损监控~~ ✅ check_stoploss
- [x] ~~每日复盘报告~~ ✅ daily_report
- [x] ~~自动调仓（嵌入 screen 选股）~~ ✅ paper.py auto-rebalance
- [x] ~~多策略账户并行~~ ✅ 已建 short/swing/trend 三账户
- [x] ~~每日 cron 入口~~ ✅ daily_runner.py（`run_all` 可编程入口）
- [x] ~~自动定时调度~~ ✅ APScheduler 集成进 FastAPI（每交易日 18:00 自动「更新行情 + daily_runner」，随 uvicorn 启动）
- [x] ~~每日评级调仓~~ ✅ 全策略每日复查，连续转弱退出，候选至少 B 级且需明显更强，否则空仓
- [x] ~~盘中实时监控~~ ✅ 短线交易时段每 10 分钟盯持仓，跌破 -8% / MA5 即清仓（盘中只卖不买）
- [ ] 跑 1-3 个月积累真实数据（需 uvicorn 进程常驻）
- [ ] 修复 pandas SQLAlchemy 警告（用 SQLAlchemy engine 替代 pymysql connection）

### 实盘持仓分析（与模拟盘隔离）✅ **新上线**
- [x] ~~实盘持仓 CRUD~~ ✅ real_holding.py（前端录入，表自动建）
- [x] ~~盘后全方位分析~~ ✅ holding_analyzer.py（5 维评级 + 当天新闻/公告/研报 + 涨跌浮盈）
- [x] ~~邮件推送~~ ✅ notify.py（SMTP，配置存 DB 由前端录入）+ 每交易日 18:30 自动发
- [ ] 板块消息（行业板块行情/资金流/新闻）—— 下期，需接入板块数据源（现 industry 字段为空）

### 人工实盘验证（当前阶段）
- [x] 交易实例、资金/持仓账务、T+1、幂等、审计与软删除
- [x] 主/影子信号体系与盘前/午间计划窗口
- [x] 延迟行情条件计划与券商报价确认回填
- [x] 版本化 002 迁移设计（待按用户授权同步 VPS MySQL）
- [ ] ETF 数据抓取、白名单初始维护与前端联调
- [ ] 连续 10 个交易日人工照抄与执行偏差记录
- [ ] 真实执行绩效、基准归因和最大回撤

### 自动下单（后续，未开始）
- [ ] 取得华泰合规 API 授权后对接券商适配器
- [ ] 订单生命周期、成交回报、对账、断线恢复与权限模型
- [ ] ML 模型（XGBoost）替代线性加权打分
- [ ] 大盘择时（沪深 300 200 日均线之下空仓/减仓）
- [ ] 行业景气度判断 + 行业轮动
- [ ] 另类数据（北向 / 龙虎榜 / 研报 LLM 摘要）

---

## 六、重要决策记录

1. **数据源策略**：AKShare 多源（东财封了 fallback 到新浪/同花顺/巨潮）
2. **股票池**：沪深主板，排除创业板/科创板/北交所/ST/上市不足 1 年
3. **调仓周期**：双周（避免手续费过度损耗，可调）
4. **风控**：单股 -8% 止损，等权持仓
5. **资金量自适应**：单股仓位 5000-100000 元区间，几何中位为推荐值
6. **过拟合教训**：单次 IC 调权重看似 +5% alpha，多窗口验证后只有 +3%（小样本不可信）
7. **持仓数设计**：不硬编码档位，按 `单股仓位约束` 公式算区间
8. **东财反爬**：spot/资金流接口被封，但单股新闻/公告/研报能通；用新浪 spot 兜底
9. **MySQL UPSERT**：pymysql `with conn` 默认 ROLLBACK，已强制 autocommit=True
10. **每日评级调仓**：所有模拟盘策略每天复查持仓；财务高风险、ST/退市风险和 D 级立即退出，普通趋势连续两个评级日转弱才退出。替换候选至少 B 级，且综合分需高出被替换股票 0.20；没有合格候选时保留现金。
11. **定时调度用 APScheduler 而非系统 cron**：BackgroundScheduler 集成进 FastAPI lifespan，随 uvicorn 启动，每交易日 18:00 串行「更新行情 → daily_runner」。优点：代码即配置、跨平台一致；前提：进程需常驻，多 worker 部署需防重复执行（已设 max_instances=1）。

---

## 七、当前局限 / 待解决

- ❗ 100 只 6 月回测仍要 ~200 秒（DB 远程网络延迟），后续可加批量查询
- ❗ 同花顺资金流只有"当日快照"，没历史数据，回测无法用
- ❗ 财务数据只能单股拉，全市场首次入库要 30-60 分钟
- ❗ universe_snapshot 表空着，回测有"幸存者偏差"
- ❗ 手续费模型 0.15% 双边是简化值（最低 5 元佣金陷阱未精确建模）
- ❗ 没有实盘验证；策略 alpha 是回测的，**没经过真实市场考验**

---

## 八、文档地图（怎么找东西）

- 想看**最新策略表现** → 此文档"3.4"
- 想看**所有可用命令** → 此文档"四"
- 想看**因子定义** → `factors/compute.py` + `single_grader.py`
- 想看**策略权重** → `strategies.py` 的 `FACTOR_PROFILES`
- 想看**回测原理** → `backtest_simple.py` 顶部 docstring
- 想看**数据库表结构** → `sql/schema.sql`
- 想改 PE 等评分阈值 → `single_grader.py` 顶部 `score_pe` / `score_pb` 等函数
- 想改关键词词典 → `news_scorer.py` 顶部 `TITLE_KEYWORDS`
- 想加新因子 → 改 `factors/compute.py` + `strategies.py` + `single_grader.py`

---

**文档最后更新提醒**：每次大改后用 1 分钟更新本文件的"二、进度总览"和"三、数据资产"两节。
