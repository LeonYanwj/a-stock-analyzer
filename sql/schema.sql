-- ==========================================================================
-- A 股量化项目数据库 schema
-- 目标库：quant_data (MariaDB 10.11)
--
-- 5 个数据域（前缀严格区分）：
--   market_*       行情/基础数据（事实，所有策略共享）
--   strategy_*     策略配置
--   backtest_*     回测/研究数据（历史模拟）
--   paper_*        模拟盘数据（虚拟资金 + 真实时间）
--   live_*         实盘数据（暂未建，未来加）
-- ==========================================================================

USE quant_data;

-- ===== 一、行情/基础数据 ===================================================

-- 1. 日线 K 线（最大表：5000 只 × 250 天/年 × N 年）
CREATE TABLE IF NOT EXISTS market_daily (
    ts_code        VARCHAR(10) NOT NULL                COMMENT '股票代码 含交易所后缀',
    trade_date     DATE        NOT NULL                COMMENT '交易日',
    adjust         VARCHAR(5)  NOT NULL DEFAULT 'qfq'  COMMENT '复权方式 qfq/hfq/raw',
    open           DECIMAL(10,3),
    high           DECIMAL(10,3),
    low            DECIMAL(10,3),
    close          DECIMAL(10,3),
    vol            BIGINT                              COMMENT '成交量（手）',
    amount         DECIMAL(20,2)                       COMMENT '成交额（元）',
    pct_chg        DECIMAL(8,4)                        COMMENT '涨跌幅（%）',
    turnover_rate  DECIMAL(8,4)                        COMMENT '换手率（%）',
    created_at     TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP   DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (ts_code, trade_date, adjust),
    INDEX idx_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '日线 K 线';

-- 2. 估值（PE/PB/市值，按日）
CREATE TABLE IF NOT EXISTS market_valuation (
    ts_code     VARCHAR(10) NOT NULL,
    trade_date  DATE        NOT NULL,
    pe          DECIMAL(10,2)                       COMMENT '市盈率（静）',
    pe_ttm      DECIMAL(10,2)                       COMMENT '市盈率 TTM',
    pb          DECIMAL(10,2)                       COMMENT '市净率',
    ps          DECIMAL(10,2)                       COMMENT '市销率',
    total_mv    DECIMAL(20,2)                       COMMENT '总市值（元）',
    circ_mv     DECIMAL(20,2)                       COMMENT '流通市值（元）',
    created_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (ts_code, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '估值指标';

-- 3. 财务摘要（季度报告期）
CREATE TABLE IF NOT EXISTS market_financial (
    ts_code        VARCHAR(10) NOT NULL,
    report_date    DATE        NOT NULL                COMMENT '报告期 YYYY-03-31 / 06-30 / 09-30 / 12-31',
    roe            DECIMAL(8,4)                        COMMENT '净资产收益率 %',
    roe_diluted    DECIMAL(8,4)                        COMMENT 'ROE 摊薄 %',
    gross_margin   DECIMAL(8,4)                        COMMENT '销售毛利率 %',
    net_margin     DECIMAL(8,4)                        COMMENT '销售净利率 %',
    net_profit     DECIMAL(20,2)                       COMMENT '净利润（元）',
    net_profit_yoy DECIMAL(8,4)                        COMMENT '净利润同比 %',
    revenue        DECIMAL(20,2)                       COMMENT '营收（元）',
    revenue_yoy    DECIMAL(8,4)                        COMMENT '营收同比 %',
    debt_ratio     DECIMAL(8,4)                        COMMENT '资产负债率 %',
    created_at     TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP   DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (ts_code, report_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '财务摘要';

-- 4. 资金流快照（按日 + 窗口存档）
CREATE TABLE IF NOT EXISTS market_fund_flow (
    snapshot_date  DATE        NOT NULL                COMMENT '快照日期',
    ts_code        VARCHAR(10) NOT NULL,
    window_label   VARCHAR(20) NOT NULL                COMMENT '即时/3日排行/5日排行/10日排行/20日排行',
    fund_inflow    DECIMAL(20,2)                       COMMENT '流入资金（元）',
    fund_outflow   DECIMAL(20,2)                       COMMENT '流出资金（元）',
    fund_net       DECIMAL(20,2)                       COMMENT '净流入（元）',
    created_at     TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date, ts_code, window_label),
    INDEX idx_ts (ts_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '资金流（按日快照）';

-- 5. 股票基础信息
CREATE TABLE IF NOT EXISTS market_stock_basic (
    ts_code     VARCHAR(10) PRIMARY KEY,
    symbol      VARCHAR(6)                          COMMENT '6 位代码',
    name        VARCHAR(20)                         COMMENT '股票简称',
    industry    VARCHAR(50)                         COMMENT '所属行业',
    area        VARCHAR(20)                         COMMENT '地区',
    list_date   DATE                                COMMENT '上市日期',
    delist_date DATE                                COMMENT '退市日期，NULL 表示仍在交易',
    is_active   TINYINT     DEFAULT 1               COMMENT '1=活跃 0=已退市',
    is_st       TINYINT     DEFAULT 0               COMMENT '1=ST 0=正常（当前状态）',
    created_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_industry (industry),
    INDEX idx_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '股票基础信息';

-- 6. 交易日历
CREATE TABLE IF NOT EXISTS market_trade_calendar (
    cal_date    DATE        PRIMARY KEY,
    is_open     TINYINT     NOT NULL DEFAULT 1      COMMENT '1=开市 0=休市',
    exchange    VARCHAR(10) DEFAULT 'SSE',
    created_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '交易日历';

-- 7. 历史股票池快照（每日，避免幸存者偏差）
CREATE TABLE IF NOT EXISTS market_universe_snapshot (
    snapshot_date DATE        NOT NULL,
    ts_code       VARCHAR(10) NOT NULL,
    is_st         TINYINT     DEFAULT 0             COMMENT '当日是否 ST',
    list_days     INT                               COMMENT '当日已上市天数',
    created_at    TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date, ts_code),
    INDEX idx_ts (ts_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '每日股票池快照';

-- ===== 二、策略配置 =======================================================

-- 8. 策略定义（一个策略 = 一组因子权重 + 参数）
CREATE TABLE IF NOT EXISTS strategy_config (
    strategy_id     INT         AUTO_INCREMENT PRIMARY KEY,
    strategy_name   VARCHAR(50) NOT NULL UNIQUE     COMMENT '策略名 short_term/swing/trend/ic_optimized 等',
    strategy_type   ENUM('research', 'paper', 'live') DEFAULT 'research' COMMENT '用途',
    description     TEXT                            COMMENT '策略说明',
    factor_weights  JSON        NOT NULL            COMMENT '因子权重 JSON',
    rebal_weeks     INT         DEFAULT 2           COMMENT '调仓周期（周）',
    top_n           INT         DEFAULT 50          COMMENT '持仓数量',
    stoploss        DECIMAL(6,4) DEFAULT -0.08      COMMENT '止损线',
    commission      DECIMAL(6,4) DEFAULT 0.0015     COMMENT '双边手续费',
    universe_limit  INT         DEFAULT 500         COMMENT '股票池规模',
    is_active       TINYINT     DEFAULT 1,
    created_at      TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP   DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '策略定义';

-- ===== 三、回测/研究数据 =================================================

-- 9. 回测运行记录（每跑一次回测一条）
CREATE TABLE IF NOT EXISTS backtest_run (
    run_id          BIGINT      AUTO_INCREMENT PRIMARY KEY,
    strategy_id     INT         NOT NULL,
    start_date      DATE        NOT NULL,
    end_date        DATE        NOT NULL,
    initial_capital DECIMAL(20,2) DEFAULT 1.0       COMMENT '初始资金（净值制 1.0）',
    final_value     DECIMAL(20,6)                   COMMENT '期末净值',
    total_return    DECIMAL(10,6),
    ann_return      DECIMAL(10,6),
    sharpe          DECIMAL(8,4),
    max_drawdown    DECIMAL(8,4),
    win_rate        DECIMAL(6,4),
    n_periods       INT                             COMMENT '调仓次数',
    note            VARCHAR(200),
    created_at      TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_strategy (strategy_id),
    INDEX idx_date (start_date, end_date),
    FOREIGN KEY (strategy_id) REFERENCES strategy_config(strategy_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '回测运行记录';

-- 10. 回测每期净值
CREATE TABLE IF NOT EXISTS backtest_equity (
    run_id        BIGINT      NOT NULL,
    rebal_date    DATE        NOT NULL,
    equity        DECIMAL(20,6),
    period_return DECIMAL(10,6),
    PRIMARY KEY (run_id, rebal_date),
    FOREIGN KEY (run_id) REFERENCES backtest_run(run_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '回测每期净值';

-- 11. 回测调仓持仓明细
CREATE TABLE IF NOT EXISTS backtest_position (
    run_id        BIGINT      NOT NULL,
    rebal_date    DATE        NOT NULL,
    ts_code       VARCHAR(10) NOT NULL,
    rank_num      INT                               COMMENT '排名',
    weight        DECIMAL(6,4)                      COMMENT '权重',
    factor_score  DECIMAL(10,4)                     COMMENT '综合打分',
    period_return DECIMAL(10,6)                     COMMENT '本期收益（已含止损）',
    stoploss_hit  TINYINT     DEFAULT 0,
    PRIMARY KEY (run_id, rebal_date, ts_code),
    INDEX idx_ts (ts_code),
    FOREIGN KEY (run_id) REFERENCES backtest_run(run_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '回测调仓明细';

-- 12. 回测每期因子 IC
CREATE TABLE IF NOT EXISTS backtest_factor_ic (
    run_id       BIGINT      NOT NULL,
    rebal_date   DATE        NOT NULL,
    factor_name  VARCHAR(50) NOT NULL,
    ic           DECIMAL(8,6),
    PRIMARY KEY (run_id, rebal_date, factor_name),
    FOREIGN KEY (run_id) REFERENCES backtest_run(run_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '回测因子 IC';

-- ===== 四、模拟盘数据（Phase 2 预建表） ==================================

-- 13. 模拟盘账户
CREATE TABLE IF NOT EXISTS paper_account (
    account_id      INT         AUTO_INCREMENT PRIMARY KEY,
    account_name    VARCHAR(50) NOT NULL UNIQUE     COMMENT '账户名 如 "短线-A"',
    strategy_id     INT         NOT NULL,
    initial_capital DECIMAL(20,2) NOT NULL,
    current_cash    DECIMAL(20,2) NOT NULL          COMMENT '可用现金',
    current_equity  DECIMAL(20,2)                   COMMENT '当前总权益（含持仓市值）',
    started_at      TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    is_active       TINYINT     DEFAULT 1,
    note            VARCHAR(200),
    FOREIGN KEY (strategy_id) REFERENCES strategy_config(strategy_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '模拟盘账户';

-- 14. 模拟盘订单（下单意图，可能未成交）
CREATE TABLE IF NOT EXISTS paper_order (
    order_id    BIGINT      AUTO_INCREMENT PRIMARY KEY,
    account_id  INT         NOT NULL,
    ts_code     VARCHAR(10) NOT NULL,
    side        ENUM('BUY', 'SELL') NOT NULL,
    order_type  ENUM('MARKET', 'LIMIT') DEFAULT 'MARKET',
    qty         INT         NOT NULL                COMMENT '股数',
    price       DECIMAL(10,3)                       COMMENT '限价（MARKET 时 NULL）',
    status      ENUM('PENDING','FILLED','CANCELLED','REJECTED') DEFAULT 'PENDING',
    reason      VARCHAR(50)                         COMMENT '下单理由 SIGNAL/STOPLOSS/REBALANCE/MANUAL',
    submit_at   TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    filled_at   TIMESTAMP   NULL,
    INDEX idx_account_date (account_id, submit_at),
    INDEX idx_ts (ts_code),
    FOREIGN KEY (account_id) REFERENCES paper_account(account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '模拟盘订单';

-- 15. 模拟盘成交（实际成交一笔）
CREATE TABLE IF NOT EXISTS paper_trade (
    trade_id    BIGINT      AUTO_INCREMENT PRIMARY KEY,
    order_id    BIGINT,
    account_id  INT         NOT NULL,
    ts_code     VARCHAR(10) NOT NULL,
    side        ENUM('BUY', 'SELL') NOT NULL,
    price       DECIMAL(10,3) NOT NULL,
    qty         INT         NOT NULL,
    amount      DECIMAL(20,2) NOT NULL              COMMENT '成交金额',
    commission  DECIMAL(10,4)                       COMMENT '佣金（含最低 5 元）',
    trade_date  DATE        NOT NULL,
    trade_time  DATETIME    NOT NULL,
    reason      VARCHAR(50)                         COMMENT '成交对应的下单理由',
    INDEX idx_account_date (account_id, trade_date),
    INDEX idx_ts_date (ts_code, trade_date),
    FOREIGN KEY (order_id) REFERENCES paper_order(order_id),
    FOREIGN KEY (account_id) REFERENCES paper_account(account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '模拟盘成交';

-- 16. 模拟盘当前持仓（汇总表，按成交动态更新）
CREATE TABLE IF NOT EXISTS paper_position (
    account_id  INT         NOT NULL,
    ts_code     VARCHAR(10) NOT NULL,
    qty         INT         NOT NULL                COMMENT '当前持仓股数',
    avg_cost    DECIMAL(10,3) NOT NULL              COMMENT '加权平均成本价',
    open_date   DATE        NOT NULL                COMMENT '首次买入日',
    last_update TIMESTAMP   DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, ts_code),
    FOREIGN KEY (account_id) REFERENCES paper_account(account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '模拟盘当前持仓';

-- 17. 模拟盘每日权益快照（用于复盘 + 净值曲线）
CREATE TABLE IF NOT EXISTS paper_equity_daily (
    account_id    INT         NOT NULL,
    trade_date    DATE        NOT NULL,
    cash          DECIMAL(20,2),
    market_value  DECIMAL(20,2)                     COMMENT '持仓市值',
    total_equity  DECIMAL(20,2)                     COMMENT '现金 + 市值',
    daily_return  DECIMAL(10,6),
    PRIMARY KEY (account_id, trade_date),
    FOREIGN KEY (account_id) REFERENCES paper_account(account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '模拟盘每日权益快照';

-- ===== 初始数据 =========================================================

-- 把现有 3 个策略灌进去（用 INSERT IGNORE 避免重复）
INSERT IGNORE INTO strategy_config
  (strategy_name, strategy_type, description, factor_weights, rebal_weeks, top_n)
VALUES
  ('short_term', 'research', '短线 1-3 天，重资金流/量价齐升/MACD',
    JSON_OBJECT('ep_ttm',0.1,'bp',0.2,'mom_30',0.6,'reversal_5',0.8,
                'low_vol',0.0,'liquidity',0.8,'main_inflow',1.5,
                'inflow_ratio',1.0,'macd_hist',1.0,'macd_slope',0.8,
                'lxsz',1.0,'pattern_score',0.8), 1, 50),
  ('swing', 'research', '波段 1-4 周，平衡型',
    JSON_OBJECT('ep_ttm',0.5,'bp',1.0,'mom_30',0.8,'reversal_5',0.5,
                'low_vol',0.6,'liquidity',0.3,'main_inflow',1.0,
                'inflow_ratio',0.8,'macd_hist',0.6,'macd_slope',0.4,
                'lxsz',0.5,'pattern_score',0.5), 2, 50),
  ('trend', 'research', '趋势 1-3 月，重 MACD/动量',
    JSON_OBJECT('ep_ttm',0.2,'bp',0.3,'mom_30',1.5,'reversal_5',-0.3,
                'low_vol',0.0,'liquidity',0.5,'main_inflow',1.0,
                'inflow_ratio',0.6,'macd_hist',1.0,'macd_slope',0.6,
                'lxsz',0.8,'pattern_score',0.3), 4, 50);
