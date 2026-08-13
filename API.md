# A 股交易实例后端 API

> 当前版本：`0.4.0`。服务地址：`http://<服务器地址>:8000`，交互式文档：`/docs`。

这是新前端交易概览页的接口契约。新页面应使用 `/api/trade-runs`，不要再把旧 `/api/accounts` 当成交易核心。

## 当前能力与边界

当前模式是“机器生成计划 + 人工在华泰证券照抄 + 回填实际成交”：

1. 用户在前端创建并启动一笔交易实例；
2. 后端冻结策略版本、资金与总仓位上限；
3. 后端生成交易计划，前端展示代码、方向、数量、价格区间、理由和数据状态；
4. 用户在券商端实际成交后，回填成交时间、价格、数量和费用；
5. 后端以该实际成交更新现金、持仓、收益和审计记录。

当前未接入华泰证券 API，**不会自动向券商下单**。免费数据只用于研究和延迟观察，不能显示为可靠实时行情或自动下单信号。范围仅为 A 股主板个股（`stock`）和 ETF（`etf`）。

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
  "asset_types":["stock","etf"]
}
```

停止请求：

```json
{"action":"pause","reason":"用户暂时停止执行"}
```

`action` 只能是 `pause` 或 `end`；`end` 后不可恢复。

## 手工执行计划

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/trade-runs/{run_id}/plans` | 受控策略服务创建计划；普通前端只读展示 |
| GET | `/api/trade-runs/{run_id}/plans` | 计划列表 |
| GET | `/api/trade-runs/{run_id}/plans/{plan_id}` | 单条计划与证据 |

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
  "blocked_reason":"QUOTE_STALE",
  "valid_from":"2026-08-13T09:30:00+08:00",
  "expires_at":"2026-08-13T10:00:00+08:00",
  "reason":"趋势和流动性条件满足；等待可信报价确认",
  "evidence":{"strategy_version":1,"signal_score":0.82}
}
```

计划状态：`generated`、`eligible`、`blocked`、`partially_filled`、`triggered`、`expired`、`cancelled`。计划允许分批成交；响应中的 `filled_qty` 达到 `suggested_qty` 后才变为 `triggered`。`data_status` 不是 `fresh` 或存在 `blocked_reason` 时，后端将计划设为 `blocked`；首期免费数据通常应显示 `delayed`，不可提示用户“已可自动下单”。

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
  "note":"华泰成交回填"
}
```

- `idempotency_key` 必填；重复发送同一个键不会重复扣款或重复记持仓。
- 买入校验现金与创建时冻结的总仓位上限；卖出校验持仓、可卖数量和 A 股 T+1。
- `plan_id` 可为空；若填写，代码和方向必须与计划一致。
- 成交、现金流水、持仓投影、计划状态和审计事件在一个数据库事务中写入。
- 首期 `source` 只应使用 `manual`。

## 持仓、绩效与审计

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/trade-runs/{run_id}/dashboard` | 单实例现金、持仓、计划/成交计数和最近事件 |
| GET | `/api/trade-runs/{run_id}/positions` | 由成交派生的当前持仓，不可直接编辑 |
| GET | `/api/trade-runs/{run_id}/performance` | 成本口径权益、收益和已实现收益 |
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

在目标 MySQL 数据库先执行 [`sql/trade_run_schema.sql`](sql/trade_run_schema.sql)。它只创建新交易实例表，不删除或改写旧 `paper_*` 表。

旧 `/api/accounts`、`/api/screen`、`/api/backtest` 等接口暂时保留给旧功能；新前端交易实例页只使用本文档的接口。
