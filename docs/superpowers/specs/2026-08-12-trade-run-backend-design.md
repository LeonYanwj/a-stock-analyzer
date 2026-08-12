# A-Share Trade-Run Backend Design

## Purpose

Rebuild the FastAPI and MySQL backend around auditable, user-started trade
runs. The immediate workflow is manual execution in Huatai Securities: the
backend generates complete plans, the user records actual fills, and the system
measures drawdown-adjusted stability, benchmark-relative returns, and plan-to-
fill deviations. The later broker-auto-trading workflow must reuse the same
strategy, risk, plan, position, and audit path via a broker adapter.

The first release does not submit broker orders, automate a broker UI, or
treat free quotes as a guaranteed real-time feed. It supports A-share main
board equities and ETFs only.

## Product Model

A strategy is a reusable versioned template. A `trade_run` is one user-created
instance of a strategy, with frozen capital, maximum exposure, asset scope,
strategy version, risk version, and data policy. The frontend is a dashboard:
the user creates and starts trade runs, sets capital buckets and exposure, sees
plans and outcomes, enters manual fills, and may stop or soft-delete a run.

The system controls selection, sizing, entries, exits, pausing, liquidation
plans, and ending while a run is active. It cannot automatically resume a
paused run. Only the user can start or restart it. A soft delete hides a run
from default views but retains its full history.

| Strategy | Holding horizon | Primary behavior |
|---|---|---|
| `short_term` | 1-3 trading days | Pre-market candidates and intraday conditional plans |
| `medium_term` | 1-4 weeks | Trend, industry strength, periodic rebalancing |
| `long_term` | 1-3 months | Trend, quality, low turnover |

Each strategy has independent runs, capital, positions, plans, and performance.
The first release permits one `running` run per strategy family and unlimited
historical, paused, ended, or deleted runs.

## Architecture

```text
Frontend dashboard -> FastAPI -> trade-run orchestration
  -> strategy/signal -> dynamic portfolio/risk -> order intent
  -> ManualAdapter now / BrokerAdapter later -> fills
  -> position, benchmark, performance, and audit services
  -> MarketDataProvider: free providers now, formal provider later
  -> MySQL
```

Existing data fetchers and factor utilities may be reused. The legacy
`paper_account`, `paper_engine`, and `/api/accounts` model is not the new
trading core and will be retired from the new API path.

## Data Rules

Research data includes trading calendars, main-board/ETF master data, industry
classification, adjusted daily bars, volume, amount, turnover, financial
reports with availability dates, and benchmark prices. Execution-reference data
includes unadjusted prices, prior close, price limits, suspension state, and
latest quote observations. Adjusted prices are for research only; unadjusted
prices are for plans and fills only.

Every decision input records provider, market timestamp, receipt timestamp,
delay, completeness, and snapshot/version ID. AKShare/Tushare/public sources
are acceptable for research and delayed observation mode. A missing timestamp,
stale quote, unknown suspension/limit state, or unconfirmed tradability blocks
a plan with an explicit code such as `QUOTE_STALE`, `LIMIT_STATUS_UNKNOWN`, or
`SUSPENSION_UNKNOWN`. It never becomes an executable/real-time claim.

## Domain Records

| Record | Purpose | Rule |
|---|---|---|
| `strategy_definition`, `strategy_version` | Strategy family and frozen algorithm fingerprint | New versions only |
| `trade_run` | One trading instance | Inputs immutable after start; state changes allowed |
| `market_data_observation` | Data used in decisions | Append only |
| `signal_plan` | Complete recommendation and evidence | Original content immutable |
| `order_intent` | Risk-approved order representation | Original intent immutable; lifecycle updates allowed |
| `execution_fill` | Manual or broker-confirmed fill | Append only; corrections compensate |
| `run_position` | Position projection | Derived and rebuildable |
| `risk_event`, `audit_event` | Abnormal conditions and state changes | Append only |
| `benchmark_snapshot` | Evaluation reference values | Append only |

Only `execution_fill` changes cash and positions. Plans and intents do not.

## State Machine

```text
draft -> running -> paused -> running
                 -> ended
draft/running/paused/ended -> deleted
```

- `draft -> running` and `paused -> running`: user only.
- `running -> paused` and `running -> ended`: system allowed with a risk/audit
  event and evidence.
- `* -> deleted`: user soft-delete only; deleted runs cannot restart.
- Start atomically checks inputs, deletion, data dependencies, and no running
  run with the same strategy.
- A pause cancels or expires outstanding plans and prevents new intents.
- An end may generate liquidation plans but cannot claim liquidation before
  corresponding fills exist.

## Plans, Risk, and Execution

The only valid flow is:

```text
strategy -> signal -> risk validation -> order intent -> adapter -> fill
```

A plan contains run/strategy version, asset and type, side, suggested quantity,
reference price, allowed range, trigger/invalidation, expiry, dynamic exit,
data evidence, signal reasons, sizing evidence, selected benchmark, and every
risk check. Its states are `generated`, `eligible`, `blocked`, `triggered`,
`expired`, and `cancelled`.

Sizing uses a reproducible versioned risk model: signal strength, volatility,
ATR, liquidity, industry concentration, existing exposure, and market state.
It must reject insufficient cash, non-lot quantities, T+1 violations,
suspension, price limits, price deviation, duplicates, stale data, and exposure
violations. Users do not configure these daily, but every result is explainable.

`ManualAdapter` publishes plans for manual execution and records manual fills
or non-fill reasons. `BrokerAdapter` later submits the same intent to an
authorized API and appends broker-confirmed fills; it cannot bypass risk.

## Runtime Schedule

All jobs use Asia/Shanghai and confirmed trade days.

| Window | Behavior |
|---|---|
| 08:45-09:20 | Validate previous data and benchmarks; generate conditional plans; block/pause on critical missing data |
| 09:30-11:30, 13:00-14:50 | Evaluate unexpired plans against observations; check freshness, price range, limits, T+1, cash and risk; block with explicit reasons when uncertain |
| 15:10-18:00 | Freeze inputs; process fills/non-fills; rebuild positions/cash/equity; compute performance and audit report |

A signal based on a day's close is usable only on a later trading day. It is
never backdated as a same-day tradable signal.

## Benchmarks and Performance

HS300 is market context, not the sole selection benchmark. Equity portfolios
use a matching broad benchmark plus industry/style context; single-stock
signals use their industry index plus broad/style comparison; ETFs use tracking
indexes and, where available, comparable ETFs.

Reports include absolute, realized and unrealized return, fees, maximum
drawdown, benchmark-relative return, plan-to-fill delay, price deviation, fill
ratio, and blocked/non-fill reasons. All values must be recomputable from
frozen inputs and fills.

## API Contract

```text
GET    /api/dashboard
GET    /api/strategy-definitions
GET    /api/strategy-definitions/{code}/versions
POST   /api/trade-runs
GET    /api/trade-runs
GET    /api/trade-runs/{run_id}
GET    /api/trade-runs/{run_id}/dashboard
POST   /api/trade-runs/{run_id}/start
POST   /api/trade-runs/{run_id}/stop
DELETE /api/trade-runs/{run_id}
GET    /api/trade-runs/{run_id}/plans
GET    /api/trade-runs/{run_id}/plans/{plan_id}
POST   /api/trade-runs/{run_id}/fills
GET    /api/trade-runs/{run_id}/positions
GET    /api/trade-runs/{run_id}/performance
GET    /api/trade-runs/{run_id}/events
GET    /api/system/data-status
```

Run creation accepts name, strategy code, capital, maximum exposure, and asset
types. Plans expose all manual-execution information and data/block status.
Fills accept plan ID, side, code, timestamp, price, quantity, fees, source,
and note; non-fills retain an outcome reason. Existing uniform error JSON is
preserved, for example `STRATEGY_RUN_ALREADY_ACTIVE`.

`API.md` becomes the authoritative frontend contract with request/response
examples, enums, pagination, error codes, and delayed-data semantics.

## Acceptance Criteria

All tests use an isolated database/schema, not existing business data.

1. State transitions prove user-only start/restart, no automatic resume,
   soft-delete non-restartability, and one running run per strategy.
2. Plans never affect cash or positions. Fill, projection, and audit writes are
   atomic. Partial, absent, duplicate, and reversing fills are deterministic.
3. Historical/replay code uses only data available at `as_of`; no same-close
   execution of close-generated signals and no `datetime.now()` dependence.
4. Stale/missing/unknown observations block plans visibly.
5. Identical strategy version, snapshot, and run input yield identical plans,
   sizes, explanations, and benchmark choice.
6. Returns, drawdown, fees, fill deviation, and benchmarks are independently
   recomputable.
7. API contract tests lock frontend fields, errors, pagination, and soft delete.

## Milestones

| Milestone | Delivery | Exclusion |
|---|---|---|
| M1 | New schema, trade-run state machine, audit, strategy-version reads | Automatic decisions |
| M2 | Manual plans, fill entry, projections, dashboard APIs | Broker submission |
| M3 | Point-in-time snapshots, reliable research/backtest, benchmarks, performance, data status | Paid real-time feed |
| M4 | Adapter interface, broker lifecycle, reconciliation, deployment hardening | Unauthorized broker connection |

M1-M3 are the first backend release. M4 begins only after manual evidence and
an authorized broker/API channel are available.
