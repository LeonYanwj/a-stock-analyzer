# 市场扫描 API：前端接入手册

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
| 策略 | `strategy_code` | `short_term`、`medium_term`、`long_term` | 选择筛选和排序规则 |
| 资产范围 | `asset_types` | `stock`、`etf`，至少选一项 | 决定扫描股票、ETF 或两者 |
| 股票覆盖 | `stock_scope` | `quick`、`full` | 有股票时，选择快速扫描或全市场扫描 |
| 扫描时段 | `plan_window` | `pre_market`、`midday` | 标记扫描窗口和对应数据截面 |

推荐中文显示名称：

```ts
export const STRATEGY_OPTIONS = [
  { value: 'short_term', label: '短线策略' },
  { value: 'medium_term', label: '中线策略' },
  { value: 'long_term', label: '长线策略' },
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
export type StrategyCode = 'short_term' | 'medium_term' | 'long_term'
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
