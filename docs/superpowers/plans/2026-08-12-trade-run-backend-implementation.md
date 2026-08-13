# 交易实例后端第一阶段实施计划

> 范围：M1 + M2。交付一个可被前端使用的“创建并启动交易实例、生成/查看手工执行计划、回填成交、派生资金与持仓、查看概览”的后端垂直切片。
>
> 不包含：券商自动下单、可靠实时行情、时点回测、完整多基准绩效和生产认证部署。这些属于后续 M3/M4，避免在账务内核尚未稳定时混入复杂外部依赖。

## 1. 实施边界和不变规则

1. 新交易实例 API 使用独立的 `trade_run` 领域，不改造旧 `/api/accounts` 语义。
2. 交易实例启动时冻结策略版本、资金与风险边界；策略版本只读，不覆盖历史版本。
3. 只有 `execution_fill`（手工成交回填）改变现金和持仓事实；计划、委托意图、系统建议不改变账务。
4. 状态迁移必须集中在领域服务中：`draft -> running -> paused -> running`，以及 `running -> ended`，任何状态可软删除但删除后不可启动。
5. 同一策略同时最多一个 `running` 实例；并发启动必须由数据库唯一约束/事务保证，而不是只依赖应用层查询。
6. 所有写账务操作在一个显式事务内完成，并记录不可变审计事件；重复回填必须幂等或返回明确错误。
7. 首期手工执行计划必须显式标注免费/延迟数据状态，数据不可信时为 `blocked`，不能显示成可自动下单信号。
8. 测试使用隔离 SQLite 适配层或临时 MySQL schema，不触碰用户既有 `quant_data` 业务数据。

## 2. 文件级改动清单

### 新增

- `trade_run/__init__.py`
- `trade_run/models.py`：领域枚举、状态迁移表、领域数据结构和序列化辅助。
- `trade_run/repository.py`：数据库方言隔离的 CRUD、事务、行锁和派生查询。
- `trade_run/service.py`：创建/启动/暂停/结束/删除、计划生成、成交回填、持仓资金重建、概览聚合。
- `api/routes/trade_runs.py`：新 API 路由和请求/响应模型。
- `tests/test_trade_run_state.py`：状态机、软删除、同策略并发约束。
- `tests/test_trade_run_accounting.py`：计划不改账、成交事务、部分成交、重复成交、超卖/现金不足。
- `tests/test_trade_run_api.py`：隔离数据库下的关键 HTTP 契约。
- `sql/trade_run_schema.sql`：新领域表、索引、约束和初始策略版本种子。

### 修改

- `api/main.py`：注册 `trade_runs.router`，根路径版本与新接口概况同步。
- `api/errors.py`：补充交易实例领域错误码（不存在、状态非法、已存在运行实例、数据阻塞、账务冲突）。
- `API.md`：重写为前端使用的交易实例接口契约；旧账户接口移入兼容/迁移说明，不再作为新流程示例。
- `requirements.txt`：只在测试适配层确有需要时补充依赖，优先复用现有 FastAPI/Pydantic/数据库依赖。

不修改：`AGENTS.md`、`MEMORY.md`、用户前端目录及旧模拟盘业务实现，除非测试证明新路由必须共享其中的只读工具。

## 3. 先写失败测试（Red）

### 3.1 状态机测试

- 创建实例默认 `draft`，保存策略代码、版本号、资金、总仓位和资产范围。
- 只有显式 `start` 能从 `draft` 进入 `running`；普通后台调用不能隐式启动。
- `running -> paused` 后不会自行回到 `running`；用户再次 `start` 才能恢复。
- `running -> ended` 后不能恢复；`deleted_at` 非空的实例不能启动、不能产生新计划。
- 同一策略第二个实例启动返回 `STRATEGY_RUN_ALREADY_ACTIVE`，并发测试只允许一个成功。

### 3.2 账务测试

- 插入 `signal_plan` 或 `order_intent` 前后，现金和持仓完全不变。
- 合法买入成交后，现金扣除成交额+费用、持仓增加、审计事件与成交记录在同一事务提交。
- 订单写入失败/持仓写入失败时，现金、成交和持仓全部回滚。
- 卖出数量超过可卖数量、非整手、现金不足、已结束实例、T+1 不可卖均返回确定错误。
- 部分成交只按实际 `fill` 派生，不把计划数量当作已成交；同一个外部成交幂等键重复提交不重复记账。
- 未成交回填只更新计划/意图状态和原因，不改变账务。

### 3.3 API 测试

- `POST /api/trade-runs`、`POST /start`、`GET /dashboard`、`GET /plans`、`POST /fills`、`GET /positions`、`GET /performance`、`GET /events` 返回固定字段和中文错误结构。
- 计划响应包含证券、方向、数量、参考价、价格区间、有效期、数据状态、阻塞原因和可复现理由。
- `DELETE /api/trade-runs/{id}` 为软删除，历史查询仍能看到 `deleted` 实例，启动返回 409/业务错误。

先运行这些测试确认失败，再实现最小领域服务；不以旧 `paper_engine` 的行为作为新领域正确性的依据。

## 4. M1：模型、表结构和状态机

1. 设计 `sql/trade_run_schema.sql`：
   - `strategy_definition`、`strategy_version`；
   - `trade_run`（资金、状态、冻结配置、启动/暂停/结束/删除时间）；
   - `market_data_observation`；
   - `signal_plan`、`order_intent`；
   - `execution_fill`；
   - `run_position`、`run_cash_ledger`；
   - `risk_event`、`audit_event`；
   - 必要的外键、唯一键、状态/实例索引和幂等键。
2. 在 `trade_run/models.py` 集中定义策略代码、资产类型、方向、计划状态、实例状态和错误语义，避免路由内散落字符串。
3. 在 `repository.py` 实现参数化 SQL；写操作显式接收事务连接，读取运行实例时支持 `FOR UPDATE`。
4. 在 `service.py` 实现状态迁移，所有迁移检查旧状态、写审计事件，并在一个事务内提交。
5. 注册策略定义/版本只读接口；首期内置短线、中线、长线三个稳定版本，版本指纹固定。
6. 完成 M1 测试后运行静态检查和隔离数据库测试，提交：`feat(trade-run): add run state and audit core`。

## 5. M2：手工计划、成交回填和概览

1. 增加手工计划服务：接受策略服务输出的候选/信号，冻结输入快照、基准和仓位计算证据；无法确认报价时生成 `blocked` 计划及数据状态。
2. 增加委托意图：只允许从合格计划生成，记录目标数量/价格区间/触发和失效条件；不直接更新现金或持仓。
3. 增加成交回填：
   - 校验实例状态、证券范围、价格/数量/交易时间、买卖方向、T+1 和重复键；
   - 以 `execution_fill` 为事实源，在单事务内追加现金流水、更新派生持仓、写风险/审计事件；
   - 对未成交记录原因和计划状态；
   - 对修正使用反向/冲销记录，不覆盖原成交。
4. 增加概览聚合：运行状态、现金、持仓市值（无行情时标注 `cost`/`stale`）、已实现/未实现收益、费用、计划数、成交数、阻塞数和最近审计事件。
5. 注册路由并更新 `api/main.py`。路由层只负责解析、鉴权预留、调用服务和映射错误，不嵌入账务计算。
6. 重写 `API.md`：每个新接口给出中文用途、请求示例、响应示例、字段定义、枚举、分页、错误码和免费行情延迟语义；明确“当前只能人工照抄，未接券商 API”。
7. 完成 M2 测试后运行验证命令，提交：`feat(trade-run): add manual plan fill and dashboard APIs`。

## 6. 隔离测试和验证命令

优先使用项目可用的 `python3`，不创建或修改生产配置。建议命令：

```bash
python3 -m compileall -q trade_run api tests
python3 -m unittest discover -s tests -p 'test_trade_run_*.py' -v
git diff --check
```

若当前环境缺少 `numpy`、`pymysql`、FastAPI 或 MySQL，不伪装为通过：先运行不依赖外部行情的领域单元测试，并在结果中标明缺失依赖；数据库集成测试通过 `TEST_DATABASE_URL`/临时 schema 显式启用。

## 7. 后续 M3/M4 入口条件

- M1/M2 所有账务和状态测试通过，且至少有一段人工照抄交易历史可复算。
- M3 再引入 `as_of` 数据快照、交易日一致性、匹配基准和收益/回撤归因。
- M4 再实现 `ManualAdapter` 之外的 `BrokerAdapter`、订单生命周期、券商回报对账、断线恢复和生产认证；在没有华泰合规 API 授权前不实现网页/App 自动化下单。
