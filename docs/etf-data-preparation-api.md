# ETF 数据准备接口：前端使用手册

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
