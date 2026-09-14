> **【第一阶段已修改】** 本文已统一收录 API、市场扫描和 ETF 数据准备接口。第一阶段新增快速/全局股票扫描，以及三个默认只读规则策略：`trend_momentum`、`breakout_volume`、`low_volatility`。市场扫描只返回候选股票，不操作仓位、账户或订单。

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

新交易实例固定使用 `signal_source: "vnpy"`：候选由已安装的 vn.py `Alpha101` 因子表达式计算并按横截面信号排序。信号格式兼容 vn.py 的股票组合策略输入；本项目继续负责计划、限仓、100 股整手、人工报价确认和成交审计。系统自动生成 `vnpy_reference` 对照候选；只有主体系计划可进入真实账务。`legacy` 和 `new` 仅用于读取既有历史实例。

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
  "signal_source":"vnpy",
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
[市场扫描 API](market-scan-api.md)。

市场扫描是“以明确选择的策略、资产范围和数据截面生成候选池”的独立只读步骤。它**不会**
读取交易实例状态，不要求启动交易，也不会创建交易计划、扣减现金或修改持仓。页面提交后
立即得到一条任务记录；随后通过任务详情轮询 `status`、`progress` 和 `progress_msg`，任务
完成后读取候选池结果。

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/market-scans` | 提交后台市场扫描；股票支持 `stock_scope=quick`（成交额前 100，只）或 `full`（全市场） |
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

在目标 MySQL 先确保基础迁移已经执行，再按版本执行 [`sql/migrations/20260813_002_signal_sources_and_etf.sql`](sql/migrations/20260813_002_signal_sources_and_etf.sql)、[`sql/migrations/20260908_001_vnpy_alpha_strategy.sql`](sql/migrations/20260908_001_vnpy_alpha_strategy.sql) 和 **【第一阶段新增】** [`sql/migrations/20260910_001_default_scan_strategies.sql`](sql/migrations/20260910_001_default_scan_strategies.sql)。迁移只新增结构，不删除或改写旧 `paper_*`、行情或回测数据。

旧 `/api/accounts` 自动调仓、`/api/screen` 和 `/api/backtest/run/async` 已停用，不会生成旧策略交易；请使用 `/api/market-scans`、`/api/trade-runs` 和 `/api/vnpy/backtests`。

---

## 【第一阶段新增】市场扫描 API：前端接入手册

## 一句话边界

市场扫描是**独立的后台研究任务**：用户选择策略、扫描范围和扫描时段，后端异步生成候选池。

它不需要交易实例，不需要点击“启动交易”，也不会创建交易计划、扣减现金或修改持仓。

```text
市场扫描任务 → 候选池结果 → 用户确认 → 后续关联交易实例 → 生成交易计划 → 人工成交回填
```

不要调用已删除的旧路径：

```text
/api/trade-runs/{run_id}/market-scans
```

统一使用本文的 `/api/market-scans`。

## 鉴权与请求约定

所有接口要求已登录会话 Cookie；网页前端使用 `credentials: 'include'`，不要在浏览器中
保存或传递 `X-API-Key`。

```ts
const API_BASE = '/api'

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: 'include',
    headers: {
      Accept: 'application/json',
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers,
    },
  })

  const body = await response.json()
  if (!response.ok) throw body
  return body as T
}
```

通用错误格式：

```json
{
  "error": "UNKNOWN_STRATEGY",
  "message": "不支持的策略代码",
  "detail": "可选定位信息"
}
```

## 页面输入与字段映射

市场扫描页必须让用户明确选择以下三项，不能从“当前交易实例”读取或继承。

| 页面控件 | 请求字段 | 可选值 | 含义 |
| --- | --- | --- | --- |
| 策略 | `strategy_code` | `trend_momentum`、`breakout_volume`、`low_volatility` | 选择第一阶段默认筛选和排序规则 |
| 资产范围 | `asset_types` | `stock`、`etf`，至少选一项 | 决定扫描股票、ETF 或两者 |
| 股票覆盖 | `stock_scope` | `quick`、`full` | 有股票时，选择快速扫描或全市场扫描 |
| 扫描时段 | `plan_window` | `pre_market`、`midday` | 标记扫描窗口和对应数据截面 |

推荐中文显示名称：

```ts
export const STRATEGY_OPTIONS = [
  { value: 'trend_momentum', label: '趋势动量' },
  { value: 'breakout_volume', label: '突破放量' },
  { value: 'low_volatility', label: '低波动趋势' },
] as const

export const SCAN_WINDOW_OPTIONS = [
  { value: 'pre_market', label: '盘前扫描' },
  { value: 'midday', label: '午间扫描' },
] as const
```

旧页面中的这类提示必须移除：

```text
请先启动本次交易，才能创建扫描记录。
```

替换为：

```text
市场扫描为只读操作，不会创建交易计划或改变持仓。
```

## TypeScript 类型

```ts
export type AssetType = 'stock' | 'etf'
export type StrategyCode = 'trend_momentum' | 'breakout_volume' | 'low_volatility'
export type PlanWindow = 'pre_market' | 'midday'
export type StockScanScope = 'quick' | 'full'
export type MarketScanTaskPhase = 'pending' | 'running' | 'done' | 'failed'

export interface MarketScanSubmitRequest {
  strategy_code: StrategyCode
  asset_types: AssetType[]
  plan_window: PlanWindow
  // 默认 quick：按当次最新成交额取前 quick_limit 只合格主板股票。
  // full：扫描全部合格主板股票。
  stock_scope?: StockScanScope
  // 仅 stock_scope='quick' 有效，范围 50–500，默认 100。
  quick_limit?: number
  // 可选。未传时由后端使用提交时刻；历史回放或固定截图时才传。
  as_of?: string
}

export interface MarketScanCandidate {
  ts_code: string
  asset_type: AssetType
  side: 'buy' | 'sell'
  candidate_status: 'eligible' | 'blocked'
  blocked_reason?: string | null
  reference_price?: number | null
  suggested_price_range?: {
    min_price?: number | null
    max_price?: number | null
  } | null
  score?: number | null
  reason: string
  data_status: string
  data_source?: string
  data_as_of?: string | null
  execution_confirmation_required?: boolean
  evidence: Record<string, unknown>
}

export interface MarketScanResult {
  plan_window: PlanWindow
  as_of: string
  strategy_code: StrategyCode
  asset_types: AssetType[]
  trading_mode: 'manual_fill'
  quote_reliability: 'not_realtime'
  message: string
  candidates: MarketScanCandidate[]
  candidate_count: number
}

export interface MarketScanProgressEvent {
  progress: number
  message: string
  at?: string
}

export interface MarketScanTask {
  task_id: string
  name: 'market_scan'
  status: MarketScanTaskPhase
  progress: number
  progress_msg?: string
  params: {
    task_type: 'market_scan'
    strategy_code: StrategyCode
    asset_types: AssetType[]
    plan_window: PlanWindow
    stock_scope?: StockScanScope | null
    quick_limit?: number | null
    as_of: string
  }
  result?: MarketScanResult
  error?: string | null
  created_at?: string
  started_at?: string | null
  finished_at?: string | null
  duration_seconds?: number | null
  from_db?: boolean
  progress_events?: MarketScanProgressEvent[]
}
```

`progress_events` 用于正在运行时的详情时间线。已完成任务在后端进程重启后从历史归档读取时，
该数组可能为空；前端应把它当作可选字段处理，而不是依赖它重建最终结果。

## 1. 提交扫描任务

`POST /api/market-scans`

请求示例：

```ts
const submitted = await request<{
  task_id: string
  status: MarketScanTaskPhase
  task: MarketScanTask
  tip: string
}>('/market-scans', {
  method: 'POST',
  body: JSON.stringify({
    strategy_code: 'medium_term',
    asset_types: ['stock', 'etf'],
    plan_window: 'pre_market',
    stock_scope: 'quick',
    quick_limit: 100,
  } satisfies MarketScanSubmitRequest),
})
```

立即响应示例：

```json
{
  "task_id": "8ed6d4f7-2f01-4cd1-8f52-40a2b73d3a62",
  "status": "running",
  "task": {
    "task_id": "8ed6d4f7-2f01-4cd1-8f52-40a2b73d3a62",
    "name": "market_scan",
    "status": "running",
    "progress": 5,
    "progress_msg": "校验扫描策略、资产范围与窗口",
    "params": {
      "task_type": "market_scan",
      "strategy_code": "medium_term",
      "asset_types": ["etf", "stock"],
      "stock_scope": "quick",
      "quick_limit": 100,
      "plan_window": "pre_market",
      "as_of": "2026-08-18T08:45:00+08:00"
    }
  },
  "tip": "轮询 GET /api/market-scans/8ed6d4f7-2f01-4cd1-8f52-40a2b73d3a62 查看进度"
}
```

前端收到响应后的唯一正确动作是：**立即把 `task` 插入扫描记录列表**，再按 `task_id` 轮询。
不要等待扫描完成后才新增任务行。

## 2. 扫描任务列表

`GET /api/market-scans?limit=30`

返回进行中任务和已归档历史任务，按创建时间倒序。列表响应不保证含 `result`；点击一行后
必须请求任务详情。

列表行建议展示字段：

| 列 | 字段 |
| --- | --- |
| 任务 ID | `task_id`，可显示前 8–12 位，但点击详情仍用完整 ID |
| 策略 | `params.strategy_code` 映射中文名称 |
| 范围 | `params.asset_types` 映射为“股票 / ETF” |
| 股票覆盖 | 股票范围含 `stock` 时，显示 `params.stock_scope`；`quick` 显示“快速扫描（前 N 只）”，`full` 显示“全市场扫描” |
| 扫描时段 | `params.plan_window` 映射为“盘前扫描 / 午间扫描” |
| 状态 | `status` |
| 进度 | `progress`，仅 `pending`、`running` 展示进度条 |
| 当前阶段 | `progress_msg` |
| 创建时间 | `created_at` |

状态显示建议：

```ts
const TASK_STATUS_LABEL = {
  pending: '等待执行',
  running: '进行中',
  done: '已完成',
  failed: '失败',
} as const
```

## 3. 任务详情与轮询

`GET /api/market-scans/{task_id}`

当用户点击任务记录时，无论任务是否完成，都调用此接口并打开详情抽屉或弹窗。

轮询示例：

```ts
async function pollMarketScan(taskId: string, onUpdate: (task: MarketScanTask) => void) {
  for (;;) {
    const task = await request<MarketScanTask>(`/market-scans/${encodeURIComponent(taskId)}`)
    onUpdate(task)

    if (task.status === 'done' || task.status === 'failed') return task
    await new Promise(resolve => window.setTimeout(resolve, 1000))
  }
}
```

运行中的详情页显示：

```text
任务 ID
策略、范围、扫描时段、数据截面
状态和 progress 进度条
progress_msg 当前阶段
progress_events 已完成阶段时间线
```

典型 `progress_events`：

```json
[
  {"progress": 0, "message": "任务已进入后台队列", "at": "2026-08-18T08:45:01"},
  {"progress": 5, "message": "校验扫描策略、资产范围与窗口", "at": "2026-08-18T08:45:01"},
  {"progress": 42, "message": "执行策略：获取历史日线：300/1200，失败 0", "at": "2026-08-18T08:45:20"}
]
```

完成后显示：

```text
result.candidate_count
result.candidates
result.as_of
result.message
```

失败后显示：

```text
task.error
```

不要把 `failed` 任务伪装成“扫描到 0 个候选”。失败代表任务未得到可信结果；0 个候选只有在
`status=done` 且 `candidate_count=0` 时才成立。

## 4. 候选结果的显示规则

任务完成后，从 `result.candidates` 划分两组：

```ts
const candidates = task.result?.candidates ?? []
const eligible = candidates.filter(item => item.candidate_status === 'eligible')
const blocked = candidates.filter(item => item.candidate_status === 'blocked')
```

主候选表展示 `eligible`：

| 页面列 | 字段 | 说明 |
| --- | --- | --- |
| 证券 | `ts_code`、`asset_type` | 当前接口不保证证券名称，前端应允许只展示代码 |
| 动作 | `side` | `buy` 显示“候选买入”，不是已下单 |
| 入选理由 | `reason` | 后端策略返回的可解释理由 |
| 评分 | `score` | 可选字段；缺失时显示 `—` |
| 参考价 / 区间 | `reference_price`、`suggested_price_range` | 研究参考，不是实时下单报价 |
| 数据截面 | `data_as_of` | 必须显示，避免误解为实时行情 |
| 状态 | `candidate_status`、`execution_confirmation_required` | 延迟数据须提示“成交前确认券商报价” |

`blocked` 候选放入“淘汰与阻止原因”区，展示 `ts_code` 和 `blocked_reason`。这些标的不应出现
“生成计划”或“买入”按钮。

市场扫描页不得显示影子策略、影子候选或策略对照字段；对外结果只有一个 `candidates` 数组。

## 错误处理

| HTTP / 错误码 | 前端提示与处理 |
| --- | --- |
| `401 UNAUTHORIZED` | 跳转或弹出登录页，保留未提交的表单选择即可 |
| `400 UNKNOWN_STRATEGY` | 提示策略无效，刷新本地策略选项或回退到中线策略 |
| `400 INVALID_ASSET_TYPES` | 阻止提交，提示至少选择“股票”或“ETF”之一 |
| `400 INVALID_PLAN_WINDOW` | 阻止提交，回退到 `pre_market` 或 `midday` |
| `400 INVALID_STOCK_SCOPE` | 阻止提交，股票覆盖仅能选“快速扫描”或“全市场扫描” |
| `400 INVALID_QUICK_LIMIT` | 阻止提交，快速扫描数量仅支持 50–500 |
| `task.status = failed` | 后台扫描失败；在任务详情显示 `task.error`，保留任务记录，允许用户重新发起新任务 |
| `503 TRADE_RUN_NOT_CONFIGURED` | 扫描器需要读取研究数据，但后端尚未配置数据库；提示“服务端数据连接尚未就绪” |
| `404 MARKET_SCAN_TASK_NOT_FOUND` | 任务可能被清理或 ID 错误；刷新任务列表，不要继续轮询 |

## 前端实现检查清单

- [ ] 扫描页包含策略下拉、股票/ETF 多选和盘前/午间下拉。
- [ ] 勾选“股票”时显示快速扫描和全市场扫描选择；默认快速扫描，固定为成交额前 100 只主板合格股票。
- [ ] “开始市场扫描”不依赖任何交易实例的 `running` 状态。
- [ ] 点击后立即在任务列表插入 `POST` 响应中的 `task`。
- [ ] 每秒轮询任务详情，更新列表行和已打开的详情页。
- [ ] 用户点击运行中任务时，能看到 `progress`、`progress_msg` 和 `progress_events`。
- [ ] 仅 `done` 时显示候选池；`failed` 时显示错误信息。
- [ ] `candidate_count=0` 与任务失败使用不同空状态文案。
- [ ] 页面不显示影子策略、影子候选或“先启动交易才能扫描”的提示。
- [ ] 前端不保存、展示或传递 `X-API-Key`。

---

## ETF 数据准备 API（合并文档）

## 目的与适用范围

市场扫描接口 `POST /api/market-scans` **只读取**已登记且已准备好的 ETF 数据，
不会自行导入 ETF，也不会自动把 ETF 纳入白名单。

因此，前端的 ETF 管理页应通过本文接口完成下面的流程：

```text
登记 ETF → 选择是否进入白名单 → 初始化历史日线 → 轮询任务完成 → 查看数据状态 → 发起 ETF 市场扫描
```

`data_state=ready` 只代表 ETF 已满足“可被扫描器读取”的基础前提；它不代表
该 ETF 一定会出现在扫描候选池。扫描器还会计算趋势、成交额等策略条件。

## 鉴权与前端请求约定

所有本文接口均要求已登录的交易管理会话，或受控脚本携带 `X-API-Key`。

- **网页页面只能使用登录会话 Cookie**：先调用 `POST /api/auth/session` 登录，之后
  在 `fetch` / Axios 中使用 `credentials: 'include'` / `withCredentials: true`。
- **不要把 `X-API-Key` 写进前端代码、构建产物或浏览器环境变量**。该方式只供调度器
  或后端受控脚本使用。
- 当前 Cookie 是 `HttpOnly`，前端不能、也不需要读取它。
- 若前后端不在同一站点，必须通过同源反向代理提供接口；当前后端 CORS 配置不允许
  浏览器跨域携带 Cookie。

示例封装：

```ts
const API_BASE = '/api'

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: 'include',
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers,
    },
  })

  const body = await response.json()
  if (!response.ok) throw body
  return body as T
}
```

错误响应统一为：

```json
{
  "error": "ETF_NOT_FOUND",
  "message": "ETF 尚未登记；请先调用 POST /api/etfs"
}
```

## TypeScript 数据类型

```ts
export type EtfDataState =
  | 'not_registered'
  | 'history_missing'
  | 'insufficient_history'
  | 'inactive'
  | 'not_whitelisted'
  | 'ready'

export interface EtfDataStatus {
  ts_code: string
  exists: boolean
  whitelist: boolean
  daily_count: number
  data_state: EtfDataState
  scan_ready: boolean
  // exists=true 时后端还会返回以下字段
  symbol?: string
  name?: string
  etf_type?: string | null
  tracking_index?: string | null
  listing_status?: string
  avg_amount?: number | null
  updated_at?: string
  first_trade_date?: string | null
  last_trade_date?: string | null
}

export interface EtfListItem {
  ts_code: string
  symbol: string
  name: string
  etf_type: string | null
  tracking_index: string | null
  listing_status: string
  whitelist: boolean
  avg_amount: number | null
  updated_at: string
}

export interface ApiTask<T = unknown> {
  task_id: string
  name: string
  status: 'pending' | 'running' | 'done' | 'failed'
  progress: number
  progress_msg: string
  result?: T
  error?: string | null
  progress_events: Array<{
    progress: number
    message: string
    at: string
  }>
}
```

## 1. 查询 ETF 列表

`GET /api/etfs`

默认只返回 **活跃且在白名单中** 的 ETF，适合扫描页展示可扫描池。

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `search` | string | - | 按代码、简称、名称、跟踪指数模糊搜索 |
| `etf_type` | string | - | 按 ETF 类型过滤 |
| `whitelist_only` | boolean | `true` | 管理页传 `false` 才能看到未入白名单的 ETF |
| `include_inactive` | boolean | `false` | 管理页传 `true` 才能看到非 active ETF |
| `limit` | number | `200` | 1–500 |

管理页建议请求：

```ts
const etfs = await request<EtfListItem[]>(
  '/etfs?whitelist_only=false&include_inactive=true&limit=500',
)
```

注意：列表接口只返回基础资料，不返回 `daily_count`。要显示某只 ETF 的历史日线状态，
请调用下节的状态接口。

## 2. 登记 ETF

`POST /api/etfs`

请求体：

```json
{
  "ts_code": "510300.SH",
  "symbol": "510300",
  "name": "沪深300ETF",
  "etf_type": "宽基",
  "tracking_index": "沪深300",
  "listing_status": "active",
  "whitelist": true,
  "avg_amount": 123456789.12
}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `ts_code` | 是 | 六位代码加交易所后缀；仅接受 `510300.SH`、`159915.SZ` 这类形式；大小写会自动规范化 |
| `symbol` | 是 | 六位证券代码 |
| `name` | 是 | ETF 显示名称 |
| `etf_type` | 否 | 例如宽基、行业、商品 |
| `tracking_index` | 否 | 跟踪指数名称 |
| `listing_status` | 否 | 默认 `active`；非 `active` 的 ETF 不会被扫描 |
| `whitelist` | 否 | 默认 `false`；只有 `true` 才会被 ETF 扫描器读取 |
| `avg_amount` | 否 | 人工维护的参考成交额 |

返回：

```json
{
  "created": true,
  "etf": {
    "ts_code": "510300.SH",
    "symbol": "510300",
    "name": "沪深300ETF",
    "whitelist": true,
    "daily_count": 0,
    "data_state": "history_missing",
    "scan_ready": false
  }
}
```

若代码已登记，调用该接口会更新本次明确提交的基础字段，并返回 `created: false`；
更推荐编辑页使用下面的 `PATCH`，避免无意改动其他字段。

## 3. 更新白名单或基础资料

`PATCH /api/etfs/{ts_code}`

最常用的操作是白名单开关：

```ts
await request(`/etfs/${encodeURIComponent('510300.SH')}`, {
  method: 'PATCH',
  body: JSON.stringify({ whitelist: true }),
})
```

可更新字段为 `symbol`、`name`、`etf_type`、`tracking_index`、`listing_status`、
`whitelist`、`avg_amount`。其中 `etf_type`、`tracking_index`、`avg_amount` 可以传
`null` 清空；`symbol`、`name`、`listing_status`、`whitelist` 不允许传 `null`。

返回：

```json
{
  "updated": true,
  "etf": {
    "ts_code": "510300.SH",
    "whitelist": true,
    "daily_count": 62,
    "data_state": "ready",
    "scan_ready": true
  }
}
```

切换白名单不会删除历史日线；从白名单移除后，后续 ETF 扫描将不再读取该 ETF。

## 4. 查询单只 ETF 的数据准备状态

`GET /api/etfs/{ts_code}/data-status`

```ts
const status = await request<EtfDataStatus>(
  `/etfs/${encodeURIComponent(tsCode)}/data-status`,
)
```

`data_state` 与页面提示建议：

| 状态 | 含义 | 建议页面操作 |
| --- | --- | --- |
| `not_registered` | ETF 尚未登记 | 显示“登记 ETF”按钮 |
| `history_missing` | 已登记，但没有日线 | 显示“初始化历史日线”按钮 |
| `insufficient_history` | 日线少于 21 根 | 显示“继续补充日线”，并显示当前 `daily_count` |
| `inactive` | `listing_status` 不为 `active` | 提示先恢复 active 状态 |
| `not_whitelisted` | 未进入扫描白名单 | 显示“加入白名单”按钮 |
| `ready` | 已登记、active、白名单、至少 21 根日线 | 可显示“可发起 ETF 扫描” |

状态优先级是先检查历史日线，再检查 active/白名单。因此一个未入白名单且没有日线的
ETF 会显示 `history_missing`，引导用户先完成日线初始化。

## 5. 初始化 ETF 历史日线

`POST /api/etfs/{ts_code}/sync-history`

这会启动后台任务，调用 AKShare 拉取日线并写入 `market_etf_daily`。接口立即返回，
不要等待日线下载完成。

默认回补最近 90 个自然日：

```ts
const started = await request<{
  task_id: string
  status: ApiTask['status']
  task: ApiTask
  tip: string
}>('/etfs/510300.SH/sync-history', { method: 'POST' })
```

也可以指定日期范围：

```ts
await request('/etfs/510300.SH/sync-history', {
  method: 'POST',
  body: JSON.stringify({
    start_date: '2026-05-20',
    end_date: '2026-08-18'
  }),
})
```

只有**已登记**的 ETF 可以启动同步；未登记时后端返回 `404 ETF_NOT_FOUND`。
同步本身不要求已入白名单，方便用户先补齐数据、再决定是否纳入扫描池。

### 轮询后台任务

使用通用任务接口 `GET /api/tasks/{task_id}` 轮询。建议间隔 1–2 秒，在
`done` 或 `failed` 时停止。

```ts
async function waitForEtfHistory(taskId: string) {
  while (true) {
    const task = await request<ApiTask<{
      ts_code: string
      start_date: string
      end_date: string
      fetched_rows: number
    }>>(`/tasks/${taskId}`)

    if (task.status === 'done') return task.result
    if (task.status === 'failed') {
      throw new Error(task.error || 'ETF 历史日线同步失败')
    }
    await new Promise(resolve => setTimeout(resolve, 1500))
  }
}
```

任务完成结果示例：

```json
{
  "ts_code": "510300.SH",
  "start_date": "2026-05-20",
  "end_date": "2026-08-18",
  "fetched_rows": 62
}
```

`fetched_rows` 是本次从数据源获得的行数。任务成功后，前端应再次调用
`GET /api/etfs/{ts_code}/data-status`，以数据库的 `daily_count` 为最终展示依据。

## 6. 发起 ETF 市场扫描

确认目标 ETF 的 `data_state=ready` 后，使用已有市场扫描接口：

```ts
await request('/market-scans', {
  method: 'POST',
  body: JSON.stringify({
    strategy_code: 'medium_term',
    asset_types: ['etf'],
    plan_window: 'pre_market'
  }),
})
```

扫描任务的进度和候选池通过 `GET /api/market-scans/{task_id}` 查询。这里的
`asset_types` 必须包含 `etf`；该接口不会导入 ETF 或补日线。

## 错误处理建议

| HTTP | `error` | 前端处理 |
| --- | --- | --- |
| 401 | `UNAUTHORIZED` | 登录会话过期，跳转登录页 |
| 404 | `ETF_NOT_FOUND` | 引导先登记 ETF |
| 422 | `INVALID_ETF_CODE` | 提示代码需为 `510300.SH` / `159915.SZ` 格式 |
| 422 | `INVALID_HISTORY_WINDOW` | 提示开始日期不能晚于结束日期 |
| 422 | `EMPTY_ETF_UPDATE` / `INVALID_ETF_UPDATE` | 提示至少修改一项，或修正非法字段值 |
| 503 | `ETF_STORAGE_UNAVAILABLE` | 提示“ETF 数据服务暂不可用，请稍后重试”；不要把数据库错误展示给用户 |
| 200 + `task.status=failed` | - | 日线任务在后台失败；保留任务编号供排查，并允许用户重试 |

## 推荐页面结构

建议新增“ETF 数据管理”页面，而不是把初始化逻辑塞进扫描页：

1. 列表区：调用 `GET /api/etfs?whitelist_only=false&include_inactive=true`。
2. 详情抽屉：调用 `data-status`，展示白名单、日线数量和最近交易日。
3. 编辑表单：调用 `POST /api/etfs` 创建，或 `PATCH /api/etfs/{ts_code}` 更新。
4. 初始化按钮：调用 `sync-history` 后显示进度条，任务结束后刷新详情状态。
5. 扫描页：仅展示或允许选择 `data_state=ready` 的 ETF，并发起
   `POST /api/market-scans`。

这样用户能明确看到“没有结果”是因为未登记、未入白名单、历史日线不足，还是策略
筛选后确实没有候选，而不会把数据准备问题误判为扫描异常。
