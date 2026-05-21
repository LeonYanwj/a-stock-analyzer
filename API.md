# API 接口文档

> **后端服务**: FastAPI 跑在 `8000` 端口
> **启动命令**: `python -m uvicorn api.main:app --host 0.0.0.0 --port 8000`
> **交互式文档**: 启动后访问 `http://localhost:8000/docs`（自动生成 Swagger UI）

## 通用约定

- **响应格式**: 全部 JSON
- **字符编码**: UTF-8
- **日期格式**: `YYYY-MM-DD`（如 `2026-05-13`）
- **时间格式**: ISO 8601（如 `2026-05-21T08:12:36`）
- **股票代码**: 含交易所后缀，如 `600487.SH` / `002028.SZ`

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

### `GET /api/accounts`
**列出所有模拟账户**

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
**创建新账户**

```bash
curl -X POST "http://localhost:8000/api/accounts?name=test-A&capital=50000&strategy=swing"
```

### `GET /api/accounts/{id}/positions`
**账户持仓**（含当日价 + 收益率）

参数：
- `asof`（可选）：查某日的持仓估值，默认今天

```bash
curl http://localhost:8000/api/accounts/1/positions
curl "http://localhost:8000/api/accounts/1/positions?asof=2026-05-13"
```

返回示例：
```json
[
  {
    "ts_code": "600094.SH",
    "qty": 2600,
    "avg_cost": 4.725,
    "current_price": 4.88,
    "return_pct": 0.0328,
    "market_value": 12688.0,
    "open_date": "2026-05-13"
  }
]
```

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
- `limit`: 股票池规模（0=全部，默认 500）
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
**单股 5 维度评级**

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
**列出最近任务**

参数：
- `limit`: 返回数量
- `name`: 任务名过滤（如 `screen`）
- `status`: pending / running / done / failed

### `GET /api/tasks/{task_id}?include_result=true&include_traceback=false`
**查单个任务**

返回字段：
- `status`: pending / running / done / failed
- `progress`: 0-100
- `progress_msg`: 当前阶段描述
- `duration_seconds`: 已运行秒数
- `result`: 任务返回值（status=done 时）
- `error`: 错误信息（status=failed 时）

### `DELETE /api/tasks/cleanup?keep=100`
**清理旧任务**

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

## ⚠️ 已知限制

1. **任务表存内存**：重启 API 服务任务会丢，结果需要查 DB 持久化数据
2. **无身份认证**：仅供个人/内网使用，**勿暴露公网**
3. **任务进度不细致**：当前只在几个关键点更新（10% / 95%），不是逐股进度
4. **CORS 全开放**：生产环境前请收紧 `allow_origins`

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
