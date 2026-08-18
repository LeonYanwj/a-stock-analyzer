# A 股交易实例后端 API

> 当前版本：`0.5.0`。服务地址：`http://<服务器地址>:8000`，交互式文档：`/docs`。

这是新前端交易概览页的接口契约。新页面应使用 `/api/trade-runs`，不要再把旧 `/api/accounts` 当成交易核心。

## 当前能力与边界

当前模式是“机器生成计划 + 人工在华泰证券照抄 + 回填实际成交”：

1. 用户在前端创建并启动一笔交易实例；
2. 后端冻结策略版本、资金与总仓位上限；
3. 后端生成交易计划，前端展示代码、方向、数量、价格区间、理由和数据状态；
4. 用户在券商端实际成交后，回填成交时间、价格、数量和费用；
5. 后端以该实际成交更新现金、持仓、收益和审计记录。

当前未接入华泰证券 API，**不会自动向券商下单**。免费数据只用于研究和延迟观察，不能显示为可靠实时行情或自动下单信号。范围仅为 A 股主板个股（`stock`）和 ETF（`etf`）。

除 `/health` 外，新交易实例和 ETF 接口都要求有效的登录会话，或供受控脚本使用的 `X-API-Key`。人工登录校验 `admin_user` 表中的固定管理员用户名和密码哈希，并创建 8 小时会话；脚本密钥只从部署环境的 `TRADE_RUN_API_KEY` 或 `config.py` 读取。管理员密码只通过初始化命令写入数据库，不得提交到 Git。

## 通用约定

- 时间为 ISO 8601，例如 `2026-08-13T09:35:00+08:00`。
- 股票代码使用 `600000.SH`、`000001.SZ` 形式。
- `side` 为 `buy`（买）或 `sell`（卖）；数量必须为正数且为 100 的整倍数。
- 金额单位是人民币元；`max_position_pct` 取值 `(0, 1]`，`0.8` 即总仓位最多 80%。
- 业务错误统一格式：

```json
{"error":"INSUFFICIENT_CASH","message":"可用现金不足","detail":"可选定位信息"}
```

## 概览、策略与数据状态

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/dashboard` | 所有未删除交易实例的首页概览 |
| GET | `/api/trade-runs/strategy-definitions` | 策略定义和当前版本 |
| GET | `/api/trade-runs/strategy-definitions/{code}/versions` | 策略历史版本 |
| GET | `/api/system/data-status` | 数据与券商执行能力声明 |

内置策略：`short_term`（短线，1–3 个交易日）、`medium_term`（中线，1–4 周）、`long_term`（长线，1–3 个月）。交易实例创建时会冻结版本，新增版本不改写历史。

创建时必须明确选择 `signal_source`：`legacy` 为旧策略映射（短线 `short_term`、中线 `swing`、长线 `trend`），`new` 为新版规则体系；另一体系自动作为 `shadow`。主体系才产生可照抄计划和真实账务。

`GET /api/system/data-status` 的关键含义：

```json
{
  "trading_mode":"manual_fill",
  "broker_order_submission":false,
  "quote_reliability":"not_realtime",
  "message":"当前仅支持人工照抄计划并回填实际成交；免费数据不能作为可靠实时下单依据。"
}
```

## 交易实例生命周期

```text
创建 draft --用户启动--> running --停止--> paused --用户再次启动--> running
                                  └--结束--> ended
任意状态 --用户逻辑删除--> deleted
```

`paused` 不会自动恢复；`deleted` 保留历史但不能重启；同一策略同时只能有一个 `running` 实例。

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/trade-runs` | 创建草稿实例 |
| GET | `/api/trade-runs?include_deleted=false` | 实例列表 |
| GET | `/api/trade-runs/{run_id}` | 单实例详情与冻结配置 |
| POST | `/api/trade-runs/{run_id}/start` | 用户唯一的启动/恢复入口 |
| POST | `/api/trade-runs/{run_id}/stop` | 暂停或结束运行实例 |
| DELETE | `/api/trade-runs/{run_id}` | 逻辑删除，历史永久保留 |

创建请求：

```json
{
  "name":"2026 年 8 月短线验证",
  "strategy_code":"short_term",
  "capital":100000,
  "max_position_pct":0.8,
  "asset_types":["stock","etf"],
  "signal_source":"legacy",
  "plan_windows":["pre_market","midday"]
}
```

停止请求：

```json
{"action":"pause","reason":"用户暂时停止执行"}
```

`action` 只能是 `pause` 或 `end`；`end` 后不可恢复。

## 计划生成、主影子比较与 ETF

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/trade-runs/{run_id}/generate-plans` | 手动测试触发盘前或午间计划 |
| GET | `/api/trade-runs/{run_id}/plans` | 计划列表 |
| GET | `/api/trade-runs/{run_id}/plans/{plan_id}` | 单条计划与证据 |
| GET | `/api/trade-runs/{run_id}/comparison` | 主影子重合与机会差异 |
| GET | `/api/etfs` | ETF 搜索、类型、跟踪指数和流动性状态 |

### 市场扫描后台任务

前端完整接入说明、TypeScript 类型、轮询代码和页面状态处理见
[docs/market-scan-api.md](docs/market-scan-api.md)。

市场扫描是“以明确选择的策略、资产范围和数据截面生成候选池”的独立只读步骤。它**不会**
读取交易实例状态，不要求启动交易，也不会创建交易计划、扣减现金或修改持仓。页面提交后
立即得到一条任务记录；随后通过任务详情轮询 `status`、`progress` 和 `progress_msg`，任务
完成后读取候选池结果。

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/market-scans` | 提交后台市场扫描，立即返回 `task_id` |
| GET | `/api/market-scans` | 市场扫描任务记录（进行中 + 已归档） |
| GET | `/api/market-scans/{task_id}` | 单条任务进度及完成后的候选池 |

提交请求：

```json
{
  "strategy_code":"medium_term",
  "asset_types":["stock","etf"],
  "plan_window":"pre_market",
  "as_of":"2026-08-17T08:45:00+08:00"
}
```

立即响应示例：

```json
{
  "task_id":"8ed6d4f7-...",
  "status":"running",
  "task":{"name":"market_scan","progress":5,"progress_msg":"校验扫描策略、资产范围与窗口"},
  "tip":"轮询 GET /api/market-scans/8ed6d4f7-... 查看进度"
}
```

任务详情中的状态为 `pending`、`running`、`done` 或 `failed`。`running` 阶段会依次报告
策略/窗口校验、执行策略扫描、股票池与历史行情加载、因子计算和候选整理。
任务详情同时返回 `progress_events` 时间线；页面可以在任务未完成时展示已经完成的阶段，
而不是只能显示最后一条进度文案。
完成时 `result` 返回 `candidates`；候选含入选理由、分数、
数据截面、数据状态、参考价和建议价格区间。`candidate_status=blocked` 的行只用于解释，
不得进入交易计划。

扫描与交易实例完全解耦，**不表示自动买入**。候选池需经用户确认，再在后续流程中选择
关联的交易实例，才能形成待人工执行的计划。

交易日调度在盘前 `08:45` 与午间 `12:45` 生成计划。同一实例、日期与窗口由数据库任务锁保证幂等；暂停、结束或删除的实例不会生成计划，失败会写入 `risk_event`。

手动测试触发：

```json
{"plan_window":"pre_market","as_of":"2026-08-13T08:45:00+08:00"}
```

```json
{
  "ts_code":"600000.SH",
  "asset_type":"stock",
  "side":"buy",
  "suggested_qty":1000,
  "reference_price":10.25,
  "min_price":10.10,
  "max_price":10.35,
  "data_status":"delayed",
  "blocked_reason":null,
  "valid_from":"2026-08-13T09:30:00+08:00",
  "expires_at":"2026-08-13T10:00:00+08:00",
  "reason":"趋势和流动性条件满足；等待可信报价确认",
  "evidence":{"strategy_version":1,"signal_score":0.82}
}
```

计划状态：`generated`、`eligible`、`blocked`、`partially_filled`、`triggered`、`expired`、`cancelled`。计划允许分批成交；响应中的 `filled_qty` 达到 `suggested_qty` 后才变为 `triggered`。`data_status=delayed` 可生成 `eligible` 条件计划，但会带有 `execution_confirmation_required=true`，绝不代表实时触发。`missing`、`stale`、`invalid` 或存在 `blocked_reason` 时才为 `blocked`。

ETF 首版只从高流动性、上市正常且已进入白名单的宽基/行业 ETF 中筛选。主影子只有同证券同方向才是 `overlap`；主计划真实成交后仅记录镜像关联。`primary_only`、`shadow_only` 只展示机会差异，不会伪造收益或改动账务。

影子计划在 `/plans` 响应中以 `signal_source` 标明，仅供展示和比较；`fills.plan_id` 只能关联主信号体系的计划。

## 实际成交回填

`POST /api/trade-runs/{run_id}/fills` 是唯一会改变现金和持仓的接口。部分成交应按实际数量逐笔回填。

```json
{
  "idempotency_key":"huatai-20260813-093500-600000-buy-001",
  "plan_id":35,
  "ts_code":"600000.SH",
  "asset_type":"stock",
  "side":"buy",
  "qty":1000,
  "price":10.25,
  "fee":5,
  "executed_at":"2026-08-13T09:35:00+08:00",
  "source":"manual",
  "broker_quote_confirmed":true,
  "quote_checked_at":"2026-08-13T09:34:30+08:00",
  "note":"华泰成交回填"
}
```

- `idempotency_key` 必填；重复发送同一个键不会重复扣款或重复记持仓。
- 买入校验现金与创建时冻结的总仓位上限；卖出校验持仓、可卖数量和 A 股 T+1。
- `plan_id` 可为空；若填写，代码和方向必须与计划一致。
- 关联延迟计划时必须提交 `broker_quote_confirmed=true` 和 `quote_checked_at`；系统会保存确认和审计记录。
- 成交、现金流水、持仓投影、计划状态和审计事件在一个数据库事务中写入。
- 首期 `source` 只应使用 `manual`。

## 持仓、绩效与审计

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/trade-runs/{run_id}/dashboard` | 单实例现金、持仓、计划/成交计数和最近事件 |
| GET | `/api/trade-runs/{run_id}/positions` | 由成交派生的当前持仓，不可直接编辑 |
| GET | `/api/trade-runs/{run_id}/performance` | 真实执行绩效、重合影子镜像状态和机会差异 |
| GET | `/api/trade-runs/{run_id}/events?limit=50` | 最近审计时间线，`limit` 为 1–200 |

没有可信实时行情时，概览的 `market_value_source` 是 `cost`，市值为成本口径；完整的实时估值、最大回撤、时点快照和匹配基准绩效属于后续 M3，当前响应会返回明确警告。

## 主要错误码

| 错误码 | 含义 |
|---|---|
| `TRADE_RUN_NOT_CONFIGURED` | 未配置数据库或未执行新表迁移 |
| `TRADE_RUN_NOT_FOUND` / `TRADE_RUN_DELETED` | 实例不存在或已删除 |
| `STRATEGY_RUN_ALREADY_ACTIVE` | 同策略已有运行实例 |
| `INVALID_RUN_TRANSITION` / `RUN_NOT_RUNNING` | 当前状态不允许操作或需要先启动 |
| `INSUFFICIENT_CASH` / `MAX_POSITION_EXCEEDED` | 买入资金或总仓位约束不通过 |
| `INSUFFICIENT_POSITION` / `T1_SELL_RESTRICTED` | 卖出持仓不足或当日买入不可卖 |
| `IDEMPOTENCY_KEY_CONFLICT` | 幂等键已属于其他实例 |
| `FILL_PLAN_MISMATCH` | 成交代码/方向和计划不一致 |
| `ASSET_TYPE_NOT_ALLOWED` | 实例未开启该资产类型 |

## 部署迁移与旧接口

在目标 MySQL 先确保基础迁移已经执行，再按版本执行 [`sql/migrations/20260813_002_signal_sources_and_etf.sql`](sql/migrations/20260813_002_signal_sources_and_etf.sql)。迁移只新增结构，不删除或改写旧 `paper_*`、行情或回测数据。

旧 `/api/accounts`、`/api/screen`、`/api/backtest` 等接口暂时保留给旧功能；新前端交易实例页只使用本文档的接口。
