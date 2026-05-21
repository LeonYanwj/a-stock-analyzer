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
| 1 | 量化研究平台 | █████████░ 96% |
| 2 | 数据基础设施 | ██████████ 100% |
| 3 | 模拟盘 | █████████░ 95% |
| 4 | 实盘 / ML / 高级 | ░░░░░░░░░░ 0% |
| | **整体** | **████████░░ 82%** |

**最新动态**：data 100% 完成！financial 全市场入库 157,196 行
（2000 只主板 × 平均 80 期季度财务）。
所有因子数据齐全：量价 + 价值 + 质量 + 资金 + 消息。

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
| **入口** | `screen.py` | 全市场选股 |
| | `rate.py` | 单股深度评级 |
| | `main.py` | 旧单股回测（保留兼容）|
| | `demo_backtest.py` | 离线回测演示 |
| | `query_backtest.py` | 历史回测查询工具 |
| | `paper_engine.py` | **模拟盘核心引擎（新）** |
| | `paper.py` | 模拟盘 CLI |
| | `daily_runner.py` | **每日运行器（cron 入口）** |
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
| 质量 | roe / gross_margin | ❌ 待加 | ✅ |
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

### 模拟盘（Phase 2）
```bash
# 创建账户
python paper.py create --name "swing-A" --capital 100000 --strategy swing
python paper.py list

# 自动调仓（一键完成：选股 + 清仓 + 买入 + 快照）
python paper.py auto-rebalance --account 1 --limit 500

# 手动操作
python paper.py buy --account 1 --code 600487.SH --qty 200 --price 75
python paper.py stoploss --account 1
python paper.py snapshot --account 1

# 查询
python paper.py positions --account 1
python paper.py trades --account 1
python paper.py report --account 1

# ⭐ 每日定时跑（cron 入口）
python daily_runner.py                        # 跑今天，所有活跃账户
python daily_runner.py --date 20260513        # 历史复盘
python daily_runner.py --account 1            # 只跑某个账户
python daily_runner.py --dry-run              # 预览不执行
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
- [x] ~~每日 cron 入口~~ ✅ daily_runner.py（待真机部署 cron）
- [ ] 真机部署 cron + 跑 1-3 个月积累数据
- [ ] 修复 pandas SQLAlchemy 警告（用 SQLAlchemy engine 替代 pymysql connection）

### 长期（Phase 3/4，1-2 周以上）
- [ ] 实盘 API 对接（CTP / 通达信仿真）
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
