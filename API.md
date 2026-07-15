# API 接口文档

> **后端服务**: FastAPI 跑在 `8000` 端口
> **启动命令**: `python -m uvicorn api.main:app --host 0.0.0.0 --port 8000`
> **交互式文档**: 启动后访问 `http://localhost:8000/docs`（自动生成 Swagger UI）
> **当前版本**: 0.3.0 | **总接口数**: 50

---

## 📑 目录

- [接口设计规则](#-接口设计规则)
- [三种调用模式](#-三种调用模式)
- [接口速查表](#-接口速查表)
- [通用约定 + 错误响应](#通用约定)
- [系统接口](#-系统接口) `/` `/health`
- [账户接口](#-账户接口模拟盘) `/api/accounts`
- [选股接口](#-选股接口) `/api/screen`
- [评级接口](#-评级接口) `/api/rate`
- [回测接口](#-回测接口) `/api/backtest`
- [股票数据接口](#-股票数据接口) `/api/stocks`
- [任务管理接口](#️-任务管理接口异步任务专用) `/api/tasks`
- [实盘持仓接口](#-实盘持仓接口) `/api/holdings` ⭐
- [通知接口](#-通知接口) `/api/notify` ⭐
- [自选股接口](#-自选股接口) `/api/watchlist` ⭐
- [常见场景示例](#-常见场景示例前端组合用法)
- [已知限制](#️-已知限制)
- [部署 cheat sheet](#-部署-cheat-sheet)

---

## 🎯 接口设计规则

| 场景 | 模式 | 例子 |
|---|---|---|
| **调外网**（AKShare / Tushare / 新浪 / 东财）| 必须 SSE 流式 | `/rate/{code}/stream`、`/positions/stream` |
| **仅查 MySQL** | 同步阻塞（毫秒级，SSE 没意义）| `/stocks/{ts_code}`、`/accounts/{id}/trades` |
| **分钟级长任务**（选股/回测/调仓）| 异步任务 + DB 归档 | `/screen/async` → 轮询 `/tasks/{id}` |

新增接口务必遵守。原因：外网调用 5-40 秒不可控，浏览器同步阻塞会卡死，前端必须有进度可见。

---

## 🔌 三种调用模式

### 模式 A：同步（GET → 立即返回）
适用：DB 查询（毫秒级）

```bash
curl "http://localhost:8000/api/accounts/1/trades?limit=10"
# → 直接返回 JSON 数组
```

### 模式 B：SSE 流式（一个连接持续推进度）
适用：外网调用（5-40 秒）

```bash
curl -N "http://localhost:8000/api/rate/600519/stream"
# data: {"progress":15,"msg":"拉历史日线..."}
# data: {"progress":50,"msg":"拉名称..."}
# data: {"progress":100,"result":{...}}
```

前端 JS：
```javascript
const ev = new EventSource('/api/rate/600519/stream');
ev.onmessage = (e) => {
  const d = JSON.parse(e.data);
  if (d.result) { showResult(d.result); ev.close(); }
  if (d.error)  { showError(d.message); ev.close(); }
  if (d.progress != null) updateBar(d.progress, d.msg);
};
```

### 模式 C：异步任务（POST 提交 → 轮询 GET）
适用：分钟级长任务（选股/回测/调仓）

```bash
# 1. 提交
curl -X POST "http://localhost:8000/api/screen/async?strategy=swing"
# → {"task_id": "xxx"}

# 2. 轮询（每 2-10 秒）
curl "http://localhost:8000/api/tasks/xxx"
# → {"status":"running","progress":30,"progress_msg":"拉股票池..."}

# 3. status=done 后拿结果
# → {"status":"done","result":{"picks":[...]}}
```

任务结果会**持久化到 DB**（`api_task_history` 表），重启 API 也能查 `/api/tasks/history`。

---

## 📋 接口速查表

| Method | Path | 模式 | 用途 |
|---|---|---|---|
| GET | `/` | A | API 根路径概况 |
| GET | `/health` | A | 健康检查 + DB 连通 |
| GET | `/api/accounts?status=active` | A | 列出账户（默认仅运行中）|
| POST | `/api/accounts` | A | 创建新模拟盘（资金可自定义）|
| GET | `/api/accounts/history` | A | 已终止模拟盘历史 ⭐ |
| GET | `/api/accounts/{id}` | A | 账户详情 |
| GET | `/api/accounts/{id}/positions` | A* | 持仓（外网 → 建议用 stream）|
| **GET** | **`/api/accounts/{id}/positions/stream`** | **B** | 持仓 SSE 流式 ⭐ |
| GET | `/api/accounts/{id}/trades` | A | 成交记录 |
| GET | `/api/accounts/{id}/equity` | A | 净值曲线 |
| GET | `/api/accounts/{id}/report` | A | 单日复盘报告 |
| POST | `/api/accounts/{id}/snapshot` | A | 保存权益快照 |
| POST | `/api/accounts/{id}/stoploss` | A | 触发止损检查 |
| POST | `/api/accounts/{id}/auto-rebalance/async` | C | 自动调仓任务 |
| POST | `/api/accounts/{id}/daily-run/async` | C | 单账户每日流程 |
| POST | `/api/accounts/{id}/terminate/async` | C | 终止模拟盘 ⭐（清仓+归档）|
| GET | `/api/screen` | A* | 选股同步（小数据量用）|
| POST | `/api/screen/async` | C | 选股异步任务 |
| GET | `/api/screen/strategies` | A | 策略列表 |
| GET | `/api/screen/optimal-top-n` | A | 资金量 → 持仓数 |
| GET | `/api/rate/{code}` | A* | 评级同步（CLI 用）|
| **GET** | **`/api/rate/{code}/stream`** | **B** | 评级 SSE 流式 ⭐ |
| GET | `/api/backtest` | A | 历史回测列表 |
| GET | `/api/backtest/{run_id}` | A | 回测详情 |
| POST | `/api/backtest/run/async` | C | 触发新回测 |
| GET | `/api/stocks` | A | 股票列表/搜索 |
| GET | `/api/stocks/{ts_code}` | A | 单股基础信息 |
| GET | `/api/stocks/{ts_code}/daily` | A | 日线数据 |
| GET | `/api/stocks/{ts_code}/valuation` | A | 估值历史 |
| GET | `/api/stocks/{ts_code}/financial` | A | 财务历史 |
| GET | `/api/tasks` | A | 内存任务列表 |
| GET | `/api/tasks/history` | A | DB 归档任务历史 |
| GET | `/api/tasks/{task_id}` | A | 单任务详情（内存 + DB fallback）|
| DELETE | `/api/tasks/cleanup` | A | 清理内存任务表 |
| GET | `/api/holdings` | A | 实盘持仓列表 ⭐ |
| POST | `/api/holdings` | A | 录入/更新持仓 ⭐ |
| PUT | `/api/holdings/{id}` | A | 修改持仓 ⭐ |
| DELETE | `/api/holdings/{id}` | A | 删除持仓 ⭐ |
| **GET** | **`/api/holdings/analyze/stream`** | **B** | 盘后全方位分析 SSE ⭐ |
| GET | `/api/notify/config` | A | 查 SMTP 配置（脱敏）⭐ |
| PUT | `/api/notify/config` | A | 设置 SMTP 配置 ⭐ |
| POST | `/api/notify/test` | A* | 发测试邮件 ⭐ |
| GET | `/api/watchlist` | A | 查询自选股 |
| POST | `/api/watchlist` | A | 添加或重新启用自选股 |
| PUT | `/api/watchlist/{id}` | A | 修改分组、策略和备注 |
| DELETE | `/api/watchlist/{id}` | A | 删除自选股 |
| POST | `/api/watchlist/report/async` | C | 生成自选股日报并按配置发邮件 |

> `A*` = 同步但调外网（仍可用，但前端建议改用 SSE 流式版）

---

## 通用约定

- **响应格式**: 全部 JSON
- **字符编码**: UTF-8
- **日期格式**: `YYYY-MM-DD`（如 `2026-05-13`）
- **时间格式**: ISO 8601（如 `2026-05-21T08:12:36`）
- **股票代码**: 含交易所后缀，如 `600487.SH` / `002028.SZ`

### 错误响应统一格式
所有错误（4xx / 5xx）都返回结构化 JSON：

```json
{
  "error":   "ACCOUNT_NOT_FOUND",      // 机器可读错误码
  "message": "账户 99999 不存在",       // 人类可读消息（中文）
  "detail":  "..."                      // 可选详情
}
```

常见错误码：

| 状态码 | error 码 | 含义 |
|:---:|---|---|
| 400 | `BAD_REQUEST` / `UNKNOWN_STRATEGY` / `INVALID_MONTHS` 等 | 请求参数业务校验失败 |
| 404 | `ACCOUNT_NOT_FOUND` / `STOCK_NOT_FOUND` / `TASK_NOT_FOUND` / `BACKTEST_NOT_FOUND` 等 | 资源不存在 |
| 422 | `VALIDATION_ERROR` | Pydantic 参数类型/格式错误 |
| 500 | `INTERNAL_ERROR` | 未捕获异常（已脱敏，不返回 Python 堆栈）|

---

## 🔧 系统接口

### `GET /`
根路径，返回 API 概况。

```bash
curl http://localhost:8000/
```

### `GET /health`
健康检查（顺便测 DB 连通）。

```bash
curl http://localhost:8000/health
# → {"status":"ok","db":"ok"}
```

---

## 📊 账户接口（模拟盘）

### 模拟盘生命周期

每个模拟盘账户 = 一次独立的策略实盘：

```
创建 (initial_capital 自定义)
   ↓
运行中 (is_active=1, 每天 daily_runner 自动调仓/止损/快照)
   ↓
点击「结束」终止 (sell all + 归档 final_equity / final_return_pct)
   ↓
归档历史 (is_active=0, GET /accounts/history 可查)
```

**同一策略可同时跑多个**（不同初始资金/不同时段实验），互不影响。
**运行中不能改 initial_capital**——想换规模就新建一个账户。

### `GET /api/accounts?status=active`
**列出模拟账户**

参数：
- `status`: `active`（默认，仅运行中）/ `terminated`（仅已终止）/ `all`（全部）

```bash
curl "http://localhost:8000/api/accounts"                   # 运行中
curl "http://localhost:8000/api/accounts?status=terminated" # 已终止
curl "http://localhost:8000/api/accounts?status=all"        # 全部
```

```bash
curl http://localhost:8000/api/accounts
```

返回示例：
```json
[
  {
    "account_id": 1,
    "account_name": "short-A",
    "strategy_name": "short_term",
    "initial_capital": 100000,
    "current_cash": 4936.24,
    "current_equity": 99109.24,
    "return_pct": -0.89,
    "started_at": "2026-05-21T01:27:26",
    "is_active": 1
  }
]
```

### `POST /api/accounts?name=xxx&capital=100000&strategy=swing`
**创建新账户**（即"下发新模拟盘任务"，资金可自定义）

```bash
curl -X POST "http://localhost:8000/api/accounts?name=test-A&capital=50000&strategy=swing"
```

### `GET /api/accounts/history` ⭐ 新
**已终止的模拟盘历史归档列表**

返回字段含初始资金、最终权益、累计收益率、持续天数、起止时间、策略：

```json
[
  {
    "account_id": 4,
    "account_name": "test-A",
    "strategy_name": "swing",
    "initial_capital": 10000.0,
    "final_equity": 10520.0,
    "final_return_pct": 0.052,
    "started_at": "2026-05-13T09:30:00",
    "ended_at": "2026-05-28T15:00:00",
    "days_run": 15,
    "note": null
  }
]
```

### `POST /api/accounts/{id}/terminate/async` ⭐ 新
**【异步】终止模拟盘**：卖光全部持仓 + 归档最终权益和累计收益率

是模拟盘的"结束"按钮对应的接口。终止后：
- `is_active=0`、`ended_at=now()`
- 计算 `final_equity` 和 `final_return_pct` 写入 `paper_account`
- 账户从 `/api/accounts?status=active` 移除，进入 `/api/accounts/history`
- **不可恢复**（重复调用会返回 400 `ACCOUNT_ALREADY_TERMINATED`）

参数：
- `use_realtime`：true（默认）拉 AKShare 实时价卖出；false 用 DB 收盘价

```bash
curl -X POST "http://localhost:8000/api/accounts/4/terminate/async"
# → {"task_id": "xxx"}
# 轮询：GET /api/tasks/xxx
```

`result` 字段示例：
```json
{
  "account_id": 4,
  "ended_at": "2026-05-28T15:00:12",
  "n_sold": 8,
  "total_revenue": 95430.5,
  "skipped": [],
  "final_cash": 95430.5,
  "remaining_market_value": 0.0,
  "final_equity": 95430.5,
  "initial_capital": 100000.0,
  "final_return_pct": -0.0457
}
```

### `GET /api/accounts/{id}/positions`
**【同步】账户持仓**（含价 + 收益率 + 价格来源标记）

⚠️ `use_realtime=true` 时调外网，可能 5-30 秒阻塞。**前端建议改用 `/positions/stream`**（SSE 流式）。

参数：
- `asof`（可选）：查某日的持仓估值，默认今天
- `use_realtime`（默认 `true`）：不传 `asof` 时优先拉 AKShare spot 拿实时价

价格选择顺序：
1. **实时价**（盘中分时价 / 盘后当日收盘）→ `price_source: "realtime"`
2. spot 拉失败时降级 DB 收盘价 → `price_source: "close"`
3. 都没有时用持仓成本 → `price_source: "cost"`

```bash
# 默认实时价
curl http://localhost:8000/api/accounts/1/positions

# 强制用 DB 历史价（更快，离线场景）
curl "http://localhost:8000/api/accounts/1/positions?use_realtime=false"

# 历史某日估值
curl "http://localhost:8000/api/accounts/1/positions?asof=2026-05-13"
```

返回示例：
```json
[
  {
    "ts_code": "600094.SH",
    "qty": 2600,
    "avg_cost": 4.725,
    "current_price": 4.54,
    "return_pct": -0.0392,
    "market_value": 11804.0,
    "open_date": "2026-05-13",
    "price_source": "realtime"
  }
]
```

### `GET /api/accounts/{id}/positions/stream` ⭐ 新
**【SSE 流式】持仓查询**（外网调用专用，让前端能看到拉数据进度）

跟同步版返回**完全相同**的数据，但分 5 个阶段推送：

```
data: {"progress": 5,   "msg": "查账户..."}
data: {"progress": 15,  "msg": "查持仓明细..."}
data: {"progress": 30,  "msg": "拉 AKShare 实时价（8 只，外网可能慢）..."}
data: {"progress": 80,  "msg": "实时价拿到 8/8 只..."}
data: {"progress": 90,  "msg": "计算收益率和市值..."}
data: {"progress": 100, "result": [...持仓数组...]}
```

参数：跟同步版完全一致（`asof` / `use_realtime`）。

```bash
curl -N "http://localhost:8000/api/accounts/1/positions/stream"
```

前端 JS：跟 `/rate/{code}/stream` 用法完全一致（EventSource）。

### `GET /api/accounts/{id}/trades?limit=50`
**成交记录**（默认最近 50 笔）

### `GET /api/accounts/{id}/equity`
**每日净值曲线**（用于画图）

### `GET /api/accounts/{id}/report?asof=2026-05-13`
**单日复盘报告**（文本格式）

### `POST /api/accounts/{id}/snapshot?asof=2026-05-13`
**保存权益快照**

### `POST /api/accounts/{id}/stoploss?asof=2026-05-13`
**触发止损检查**

### `POST /api/accounts/{id}/auto-rebalance/async` ⭐ 新
**【异步】自动调仓**：全市场评级 + 持仓日评 + 择优替换 + 保存快照。没有足够强的候选时保留现金，不会强制满仓。

参数：
- `limit`: 股票池规模（`0`=全部沪深主板，默认 `0`）
- `asof`: 截面日期 YYYY-MM-DD（可选，默认今天）
- `enable_news`: 是否启用消息面（默认 false）

```bash
curl -X POST "http://localhost:8000/api/accounts/1/auto-rebalance/async?limit=0"
# → {"task_id":"xxx","status":"running","tip":"轮询 GET /api/tasks/xxx"}
```

`result` 字段示例：
```json
{
  "account_id": 1,
  "asof": "2026-05-21",
  "strategy": "swing",
  "sold":   { "n": 8, "revenue": 92341.5 },
  "bought": { "n": 8, "spent": 91020.3, "skipped": [] },
  "picks":  ["600094.SH", "002028.SZ"],
  "total_equity": 95720.6
}
```

### `POST /api/accounts/{id}/daily-run/async` ⭐ 新
**【异步】跑单账户每日流程**：止损 → 每日评级复查 → 择优替换 → 快照 → 复盘

等价命令行：`python daily_runner.py --account {id} --date YYYYMMDD`

参数：
- `asof`: 日期（可选，默认今天）
- `limit`: 选股阶段股票池规模（`0`=全部沪深主板，默认 `0`）
- `dry_run`: true 时只看会做什么，不写入（默认 false）

```bash
curl -X POST "http://localhost:8000/api/accounts/1/daily-run/async?asof=2026-05-13&dry_run=true"
```

`result.log` 是完整的文本输出（4 个阶段日志），前端可直接展示。

---

## 🎯 选股接口

### 同步版 `GET /api/screen`
**⚠️ 注意**: 跑全市场会很慢（5-10 分钟），浏览器 30 秒会超时。
**网页用户请用异步版** ↓

```bash
curl "http://localhost:8000/api/screen?strategy=swing&capital=100000&limit=200"
```

参数：
- `strategy`: short_term / swing / trend / ic_optimized
- `capital`: 资金量（元），用于自动算 top_n
- `top`: 直接指定持仓数（覆盖 capital 自动算）
- `limit`: 股票池规模（`0`=全部沪深主板，默认 `0`）
- `lookback`: 历史回看天数（默认 60）
- `enable_news`: 是否消息面精筛（true/false）

### 异步版 `POST /api/screen/async` ⭐推荐
**浏览器友好版本**：立即返回 task_id，不会卡死。

```bash
# 1. 提交任务
curl -X POST "http://localhost:8000/api/screen/async?strategy=swing&capital=100000"
# → {"task_id":"xxx","status":"running","tip":"GET /api/tasks/xxx"}

# 2. 轮询查进度（前端可以每 2 秒查一次）
curl "http://localhost:8000/api/tasks/xxx"
# → {"status":"running","progress":10,"progress_msg":"..."}

# 3. status=done 时拿结果
# → {"status":"done","result":{"strategy":"swing","top_n":8,"picks":[...]}}
```

### `GET /api/screen/strategies`
**支持的策略列表**

```bash
curl http://localhost:8000/api/screen/strategies
# → {"strategies":["short_term","swing","trend","ic_optimized"]}
```

### `GET /api/screen/optimal-top-n?capital=100000`
**根据资金量算建议持仓数**

```bash
curl "http://localhost:8000/api/screen/optimal-top-n?capital=100000"
# → {"capital":100000,"top_n":8}
```

---

## ⭐ 评级接口

### `GET /api/rate/{code}?strategy=swing&no_flow=false&no_news=false`
**【同步】单股 5 维度评级**（一次性返回，慢的话浏览器会卡 5-40 秒）

```bash
curl "http://localhost:8000/api/rate/002028?strategy=swing"
curl "http://localhost:8000/api/rate/600487.SH?strategy=trend&no_news=true"
```

返回示例：
```json
{
  "ts_code": "002028.SZ",
  "name": "思源电气",
  "asof": "20260518",
  "strategy": "swing",
  "overall_stars": 2.66,
  "grade": "C",
  "dimensions": [
    {
      "key": "tech",
      "label": "量价面",
      "stars": 2.80,
      "weight": 1.0,
      "factors": [
        {"key": "mom_30", "stars": 3, "desc": "近30日震荡 -4.7%"}
      ]
    }
  ]
}
```

### `GET /api/rate/{code}/stream?strategy=swing&no_flow=false&no_news=false` ⭐ 新
**【SSE 流式】单股评级实时进度推送**

一个 HTTP 连接保持打开，分阶段推送 SSE 事件（前端能看到"拉历史/算因子/拉估值/拉财务/算评级..."逐步推进，不会以为卡死）。

#### 事件 schema

每个事件以 `data: <json>\n\n` 行格式发送（标准 SSE）：

| 字段组合 | 含义 |
|---|---|
| `{progress: int, msg: str}` | 进度更新（progress 0-100，msg 当前阶段描述）|
| `{progress: 100, result: {...}}` | 最终评级结果（同步版的 JSON 结构）|
| `{error: str, message: str}` | 失败（如 STOCK_DATA_EMPTY），后端自动关闭连接 |

#### 命令行测试（用 curl -N 关闭缓冲）

```bash
curl -N "http://localhost:8000/api/rate/600519/stream?strategy=swing"
```

输出示例：
```
data: {"progress": 3, "msg": "解析股票代码..."}

data: {"progress": 15, "msg": "拉历史日线 600519.SH..."}

data: {"progress": 30, "msg": "计算量价因子..."}

data: {"progress": 50, "msg": "拉名称 spot..."}

data: {"progress": 100, "result": {"ts_code": "600519.SH", "name": "贵州茅台", ...}}
```

#### 前端 JS 用法

浏览器原生 `EventSource` API，**不需要轮询**：

```javascript
const ev = new EventSource('/api/rate/600519/stream?strategy=swing');

ev.onmessage = (e) => {
  const d = JSON.parse(e.data);

  if (d.error) {
    showError(d.message);
    ev.close();
    return;
  }

  if (d.result) {
    showRating(d.result);  // 完整评级结果，跟同步版 schema 一致
    ev.close();            // ⚠️ 拿到结果一定要关，否则连接挂着
    return;
  }

  if (d.progress != null) {
    progressBar.value = d.progress;
    progressLabel.text = d.msg;
  }
};

ev.onerror = (e) => {
  ev.close();
};
```

#### 注意事项

- 若过 nginx 反代，需要 `proxy_buffering off`（响应头已经带了 `X-Accel-Buffering: no`，nginx 1.5.6+ 会识别）
- IE 不支持 EventSource，要兼容老浏览器用 polyfill
- 拿到 `result` 或 `error` 后**前端务必 `ev.close()`**，否则浏览器保持连接

---

## 🔬 回测接口

### `GET /api/backtest?limit=50&strategy=swing`
**历史回测列表**

```bash
curl "http://localhost:8000/api/backtest?strategy=swing&limit=20"
```

### `GET /api/backtest/{run_id}`
**回测详情**（含 equity 曲线 + positions + 因子 IC 汇总）

```bash
curl http://localhost:8000/api/backtest/1
```

### `POST /api/backtest/run/async` ⭐ 新
**【异步】触发一次回测**：起子进程跑 `backtest_simple.py`，跑完自动入库

参数：
- `strategy`: short_term / swing / trend / ic_optimized（默认 swing）
- `months`: 回测月数 1-60（默认 12）
- `limit`: 股票池规模（默认 300）
- `top`: 每周选股数（0=按 capital 自动算）
- `capital`: 模拟资金量（元）；传了启用精确成本模型
- `rebal_weeks`: 调仓间隔周数（1=每周, 2=两周, 4=每月，默认 1）

```bash
curl -X POST "http://localhost:8000/api/backtest/run/async?strategy=swing&months=12&capital=100000"
# → {"task_id":"xxx"}
```

`result` 字段示例：
```json
{
  "strategy": "swing",
  "months": 12,
  "limit": 300,
  "elapsed_seconds": 287.4,
  "run_id": 42,                      // 新建的回测 ID，可用 GET /api/backtest/{run_id} 查详情
  "log_tail": "..."                  // 最后 30 行日志
}
```

⚠️ 回测一次可能要 **几分钟到十几分钟**（取决于 limit 和 months），前端轮询间隔可设 10 秒+。

---

## 📈 股票数据接口

### `GET /api/stocks?search=平安&active_only=true&limit=200`
**股票搜索/列表**

```bash
curl "http://localhost:8000/api/stocks?search=平安"
curl "http://localhost:8000/api/stocks?industry=银行&limit=50"
```

### `GET /api/stocks/{ts_code}`
**单股基础信息**

### `GET /api/stocks/{ts_code}/daily?limit=90`
**日线数据**（从 DB 直接查）

```bash
curl "http://localhost:8000/api/stocks/002028.SZ/daily?limit=30"
curl "http://localhost:8000/api/stocks/002028.SZ/daily?start_date=2024-01-01&end_date=2024-12-31"
```

### `GET /api/stocks/{ts_code}/valuation?limit=30`
**估值历史（PE/PB）**

### `GET /api/stocks/{ts_code}/financial?limit=20`
**财务历史（ROE/毛利率）**

---

## ⏱️ 任务管理接口（异步任务专用）

### `GET /api/tasks?limit=30&name=screen&status=running`
**列出内存里的最近任务**（看进行中和刚跑完的）

参数：
- `limit`: 返回数量
- `name`: 任务名过滤（如 `screen` / `auto_rebalance` / `daily_run` / `backtest`）
- `status`: pending / running / done / failed

### `GET /api/tasks/{task_id}?include_result=true&include_traceback=false`
**查单个任务**（先查内存，没有就降级到 DB 归档）

返回字段：
- `status`: pending / running / done / failed
- `progress`: 0-100
- `progress_msg`: 当前阶段描述
- `params`: 提交时的入参快照（DB 归档里能看到当时怎么跑的）
- `duration_seconds`: 已运行秒数
- `result`: 任务返回值（status=done 时）
- `error`: 错误信息（status=failed 时）
- **`from_db`**: `false` 在内存里 / `true` 已归档到 DB（API 重启后仍可查）

### `GET /api/tasks/history?name=&status=&limit=30` ⭐ 新
**从 DB 归档表查历史任务**（重启不丢，跨 API 进程可见）

只有 `done` / `failed` 的任务会归档；运行中的任务仍只在内存。每条记录返回基础字段，要详细 `result/params` 需要 `GET /api/tasks/{task_id}`。

```bash
curl "http://localhost:8000/api/tasks/history?name=backtest&status=done&limit=20"
```

### `DELETE /api/tasks/cleanup?keep=100`
**清理内存任务表**（DB 归档不动）

---

## 💼 实盘持仓接口

> 记录**真实持仓**（与模拟盘 `/api/accounts` 完全隔离）。盘后 18:30 自动对每只持仓做
> 全方位分析并邮件推送，也可手动触发。表由后端自动建，无需手动跑 schema。

### `GET /api/holdings`
实盘持仓列表。返回 `[{holding_id, ts_code, name, qty, cost, buy_date, note}]`

### `POST /api/holdings`
录入/更新一只持仓（同代码已存在则覆盖）。Body(JSON)：
```json
{"code":"600036","qty":500,"cost":38.0,"buy_date":"2026-05-20","note":"可选"}
```
- `code` 支持 6 位或带后缀，自动补全为 ts_code；`name` 不传则自动查
- 返回 `{ok:true, holding_id}`

### `PUT /api/holdings/{id}`
修改持仓（部分字段）。Body：`{qty?, cost?, buy_date?, name?, note?}`

### `DELETE /api/holdings/{id}`
删除持仓。返回 `{ok:true, deleted:id}`；不存在 → `404 HOLDING_NOT_FOUND`

### `GET /api/holdings/analyze/stream?strategy=swing&send=true` ⭐ SSE
【模式 B】手动触发盘后全方位分析（调外网，分阶段推进度）。
- 每只持仓：5 维评级 + 当天新闻/公告/研报原文 + 价格/涨跌/浮盈
- `send=true` 同时发邮件（需先配好 `/api/notify/config`）
- 事件：`{progress,msg}` 进度 / `{progress:100,result:{count,asof,mail,analyses}}` 完成 / `{error,message}` 失败

---

## 📧 通知接口

> SMTP 邮件配置（**前端录入，存 DB**，不写死 config）。盘后分析报告发到这里。

### `GET /api/notify/config`
读 SMTP 配置（密码脱敏为 `******`）。未配置返回 `{configured:false}`

### `PUT /api/notify/config`
设置 SMTP 配置。Body(JSON)：
```json
{"smtp_host":"smtp.qq.com","smtp_port":465,"smtp_user":"you@qq.com",
 "smtp_pass":"授权码","mail_to":"to@xx.com","enabled":true}
```
- `smtp_pass` 用邮箱的**授权码/应用密码**，不是登录密码
- QQ邮箱 `smtp.qq.com:465`(SSL)，163 `smtp.163.com:465`

### `POST /api/notify/test`
发一封测试邮件验证配置。成功 `{ok:true,to}`；失败 → `400 MAIL_SEND_FAILED`（reason 含 SMTP 报错）

---

## 自选股接口

自选股与实盘持仓、模拟盘相互独立。每只自选股可以指定分组、评级策略和备注。
交易日 19:00 自动汇总价格、评级变化、趋势、财务风险、新闻、公告和研报并发送邮件。

### `GET /api/watchlist?active_only=true`
查询自选股列表。

### `POST /api/watchlist`
添加或重新启用自选股。Body：

```json
{"code":"600036","group_name":"银行","strategy":"swing","note":"等待回调"}
```

### `PUT /api/watchlist/{id}`
修改 `name`、`group_name`、`strategy`、`note` 或 `is_active`。

### `DELETE /api/watchlist/{id}`
删除一条自选股记录。

### `POST /api/watchlist/report/async?send=true`
异步生成日报；`send=true` 时复用 `/api/notify/config` 的 SMTP 配置发送邮件。
返回 `task_id`，通过 `/api/tasks/{task_id}` 查询进度和结果。

---

## 🌐 前端建议的轮询模式

```javascript
async function runScreenAsync() {
  // 1. 提交任务
  const { task_id } = await fetch(
    '/api/screen/async?strategy=swing&capital=100000',
    { method: 'POST' }
  ).then(r => r.json());

  // 2. 轮询（每 2 秒查一次）
  while (true) {
    await new Promise(r => setTimeout(r, 2000));
    const task = await fetch(`/api/tasks/${task_id}`).then(r => r.json());

    updateProgressBar(task.progress, task.progress_msg);  // 更新 UI

    if (task.status === 'done') {
      showPicks(task.result.picks);
      break;
    }
    if (task.status === 'failed') {
      showError(task.error);
      break;
    }
  }
}
```

---

## 💡 常见场景示例（前端组合用法）

### 场景 1：账户首页（账户列表 + 选中后看持仓）

```javascript
// 1. 加载账户下拉
const accounts = await fetch('/api/accounts').then(r => r.json());

// 2. 用户选了账户 1 → 同时拉账户元信息 + 流式拉持仓
const acc = await fetch('/api/accounts/1').then(r => r.json());
showHeader(`${acc.account_name} | 总权益 ${acc.current_equity}`);

const ev = new EventSource('/api/accounts/1/positions/stream');
ev.onmessage = e => {
  const d = JSON.parse(e.data);
  if (d.result) { renderPositions(d.result); ev.close(); }
  else if (d.progress != null) updateLoading(d.progress, d.msg);
};
```

### 场景 2：跑选股 → 拿结果 → 下单调仓

```javascript
// 1. 提交选股任务
const { task_id } = await fetch('/api/screen/async?strategy=swing&capital=100000',
                                 { method: 'POST' }).then(r => r.json());

// 2. 轮询直到完成
let result;
while (true) {
  await new Promise(r => setTimeout(r, 3000));
  const t = await fetch(`/api/tasks/${task_id}`).then(r => r.json());
  updateProgress(t.progress, t.progress_msg);
  if (t.status === 'done')   { result = t.result; break; }
  if (t.status === 'failed') { throw new Error(t.error); }
}

// 3. 用户确认 picks 后触发调仓
const { task_id: rebalId } = await fetch(
  '/api/accounts/1/auto-rebalance/async',
  { method: 'POST' }
).then(r => r.json());
// 再次轮询 rebalId ...
```

### 场景 3：单股评级页面（含实时进度）

```javascript
const code = '600519';
const ev = new EventSource(`/api/rate/${code}/stream?strategy=swing`);
ev.onmessage = (e) => {
  const d = JSON.parse(e.data);
  if (d.error) {
    showError(d.message);          // STOCK_DATA_EMPTY 等
    ev.close();
  } else if (d.result) {
    renderRating(d.result);         // 5 维度评级表格
    ev.close();
  } else {
    updateProgressBar(d.progress);   // "拉历史日线..." "拉财务..."
    updateProgressLabel(d.msg);
  }
};
```

### 场景 4：回测历史复盘

```javascript
// 1. 列出最近回测
const runs = await fetch('/api/backtest?strategy=swing&limit=20').then(r => r.json());

// 2. 用户点开某次回测
const detail = await fetch(`/api/backtest/${runs[0].run_id}`).then(r => r.json());
drawEquityCurve(detail.equity);        // 净值曲线
showICTable(detail.ic_summary);         // 因子 IC
showPositions(detail.positions);        // 每期持仓 Top
```

### 场景 5：任务历史复盘（看上次怎么跑的）

```javascript
// DB 归档查所有 backtest 任务（重启不丢）
const history = await fetch('/api/tasks/history?name=backtest&status=done&limit=10')
                .then(r => r.json());
// 看 params 字段就知道当时是什么参数跑的
history.forEach(t => console.log(t.task_id, t.params, t.duration_seconds));
```

---

## ⚠️ 已知限制

1. **进行中的任务在内存**：API 重启后正在跑的任务会丢失。已完成（done/failed）的任务已归档到 `api_task_history`，可通过 `GET /api/tasks/{id}` 或 `GET /api/tasks/history` 查到。
2. **无身份认证**：仅供个人/内网使用，**勿暴露公网**
3. **任务进度不细致**：当前只在几个关键点更新（10% / 95%），不是逐股进度
4. **CORS 全开放**：生产环境前请收紧 `allow_origins`
5. **实时价依赖外网**：盘中拉 AKShare spot 偶尔会超时；接口已自动降级到 DB 收盘价（`price_source` 字段会反映来源）

---

## 🚀 部署 cheat sheet

```bash
# VPS 后台跑
nohup python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 > api.log 2>&1 &

# 或 systemd 服务（推荐）
# /etc/systemd/system/quant-api.service
[Unit]
Description=Quant API
After=network.target

[Service]
WorkingDirectory=/path/to/a-stock-analyzer
ExecStart=/usr/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
Restart=always
User=YOUR_USER

[Install]
WantedBy=multi-user.target

# systemctl enable --now quant-api
```
