-- ============================================================================
-- 交易实例领域（M1 + M2）
-- 适用：MySQL 8.0+/MariaDB 10.11，执行前请确认目标为隔离测试库或已备份的数据库。
-- 本脚本只创建新表，不修改或删除旧 paper_* 表。
-- ============================================================================

CREATE TABLE IF NOT EXISTS strategy_definition (
    code            VARCHAR(32) PRIMARY KEY,
    name            VARCHAR(64) NOT NULL,
    description     VARCHAR(255) NOT NULL,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='交易策略定义';

CREATE TABLE IF NOT EXISTS strategy_version (
    version_id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    strategy_code           VARCHAR(32) NOT NULL,
    version_no              INT NOT NULL,
    algorithm_fingerprint   VARCHAR(128) NOT NULL,
    config_json             JSON NOT NULL,
    created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_strategy_version (strategy_code, version_no),
    CONSTRAINT fk_strategy_version_definition FOREIGN KEY (strategy_code)
        REFERENCES strategy_definition(code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='策略不可变版本';

CREATE TABLE IF NOT EXISTS trade_run (
    run_id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    name                    VARCHAR(100) NOT NULL,
    strategy_code           VARCHAR(32) NOT NULL,
    strategy_version_id     BIGINT NOT NULL,
    status                  VARCHAR(16) NOT NULL,
    -- MySQL 没有 partial unique index；运行时填策略代码，其余状态为 NULL，保证同策略仅一个 running。
    active_strategy_code    VARCHAR(32) NULL UNIQUE,
    initial_capital         DECIMAL(20,2) NOT NULL,
    current_cash            DECIMAL(20,2) NOT NULL,
    max_position_pct        DECIMAL(8,6) NOT NULL,
    asset_types_json        JSON NOT NULL,
    frozen_config_json      JSON NOT NULL,
    created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at              DATETIME NULL,
    paused_at               DATETIME NULL,
    ended_at                DATETIME NULL,
    deleted_at              DATETIME NULL,
    INDEX idx_run_status (status, deleted_at),
    INDEX idx_run_strategy (strategy_code, created_at),
    CONSTRAINT fk_run_strategy FOREIGN KEY (strategy_code) REFERENCES strategy_definition(code),
    CONSTRAINT fk_run_version FOREIGN KEY (strategy_version_id) REFERENCES strategy_version(version_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户启动的一笔独立交易实例';

CREATE TABLE IF NOT EXISTS market_data_observation (
    observation_id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id                  BIGINT NOT NULL,
    ts_code                 VARCHAR(16) NOT NULL,
    source                  VARCHAR(64) NOT NULL,
    market_time             DATETIME NULL,
    received_at             DATETIME NOT NULL,
    delay_seconds           INT NULL,
    completeness            VARCHAR(16) NOT NULL,
    snapshot_ref            VARCHAR(128) NULL,
    payload_json            JSON NOT NULL,
    INDEX idx_observation_run (run_id, received_at),
    CONSTRAINT fk_observation_run FOREIGN KEY (run_id) REFERENCES trade_run(run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='决策数据元信息与快照';

CREATE TABLE IF NOT EXISTS signal_plan (
    plan_id                 BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id                  BIGINT NOT NULL,
    ts_code                 VARCHAR(16) NOT NULL,
    asset_type              VARCHAR(16) NOT NULL,
    side                    VARCHAR(8) NOT NULL,
    suggested_qty           INT NOT NULL,
    reference_price         DECIMAL(16,4) NOT NULL,
    min_price               DECIMAL(16,4) NULL,
    max_price               DECIMAL(16,4) NULL,
    status                  VARCHAR(16) NOT NULL,
    data_status             VARCHAR(16) NOT NULL,
    blocked_reason          VARCHAR(64) NULL,
    valid_from              DATETIME NULL,
    expires_at              DATETIME NULL,
    filled_qty              INT NOT NULL DEFAULT 0,
    reason                  TEXT NOT NULL,
    evidence_json           JSON NOT NULL,
    created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_plan_run_status (run_id, status, created_at),
    INDEX idx_plan_symbol (ts_code, created_at),
    CONSTRAINT fk_plan_run FOREIGN KEY (run_id) REFERENCES trade_run(run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='供人工照抄的交易计划';

CREATE TABLE IF NOT EXISTS order_intent (
    intent_id               BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id                  BIGINT NOT NULL,
    plan_id                 BIGINT NOT NULL,
    status                  VARCHAR(16) NOT NULL,
    target_qty              INT NOT NULL,
    min_price               DECIMAL(16,4) NULL,
    max_price               DECIMAL(16,4) NULL,
    risk_result_json        JSON NOT NULL,
    created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_intent_run_status (run_id, status),
    CONSTRAINT fk_intent_run FOREIGN KEY (run_id) REFERENCES trade_run(run_id),
    CONSTRAINT fk_intent_plan FOREIGN KEY (plan_id) REFERENCES signal_plan(plan_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='通过风控的委托意图，不直接改账';

CREATE TABLE IF NOT EXISTS execution_fill (
    fill_id                 BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id                  BIGINT NOT NULL,
    plan_id                 BIGINT NULL,
    idempotency_key         VARCHAR(100) NOT NULL,
    ts_code                 VARCHAR(16) NOT NULL,
    side                    VARCHAR(8) NOT NULL,
    qty                     INT NOT NULL,
    price                   DECIMAL(16,4) NOT NULL,
    fee                     DECIMAL(16,4) NOT NULL DEFAULT 0,
    executed_at             DATETIME NOT NULL,
    source                  VARCHAR(16) NOT NULL DEFAULT 'manual',
    note                    VARCHAR(500) NULL,
    created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_fill_idempotency (idempotency_key),
    INDEX idx_fill_run_time (run_id, executed_at),
    CONSTRAINT fk_fill_run FOREIGN KEY (run_id) REFERENCES trade_run(run_id),
    CONSTRAINT fk_fill_plan FOREIGN KEY (plan_id) REFERENCES signal_plan(plan_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='实际成交事实，仅此表可改变账务事实';

CREATE TABLE IF NOT EXISTS run_position (
    run_id                  BIGINT NOT NULL,
    ts_code                 VARCHAR(16) NOT NULL,
    asset_type              VARCHAR(16) NOT NULL,
    qty                     INT NOT NULL,
    sellable_qty            INT NOT NULL,
    avg_cost                DECIMAL(16,6) NOT NULL,
    realized_pnl            DECIMAL(20,2) NOT NULL DEFAULT 0,
    open_date               DATE NOT NULL,
    updated_at              DATETIME NOT NULL,
    PRIMARY KEY (run_id, ts_code),
    CONSTRAINT fk_position_run FOREIGN KEY (run_id) REFERENCES trade_run(run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='由成交派生的持仓投影';

CREATE TABLE IF NOT EXISTS run_cash_ledger (
    ledger_id               BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id                  BIGINT NOT NULL,
    fill_id                 BIGINT NULL,
    entry_type              VARCHAR(32) NOT NULL,
    amount                  DECIMAL(20,2) NOT NULL,
    balance_after           DECIMAL(20,2) NOT NULL,
    created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_ledger_run (run_id, ledger_id),
    CONSTRAINT fk_ledger_run FOREIGN KEY (run_id) REFERENCES trade_run(run_id),
    CONSTRAINT fk_ledger_fill FOREIGN KEY (fill_id) REFERENCES execution_fill(fill_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='现金流水';

CREATE TABLE IF NOT EXISTS risk_event (
    risk_event_id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id                  BIGINT NOT NULL,
    event_code              VARCHAR(64) NOT NULL,
    severity                VARCHAR(16) NOT NULL,
    message                 VARCHAR(500) NOT NULL,
    payload_json            JSON NOT NULL,
    created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_risk_run_time (run_id, created_at),
    CONSTRAINT fk_risk_run FOREIGN KEY (run_id) REFERENCES trade_run(run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='风控与数据阻塞事件';

CREATE TABLE IF NOT EXISTS audit_event (
    event_id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id                  BIGINT NOT NULL,
    event_type              VARCHAR(64) NOT NULL,
    message                 VARCHAR(500) NOT NULL,
    payload_json            JSON NOT NULL,
    created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_audit_run_time (run_id, event_id),
    CONSTRAINT fk_audit_run FOREIGN KEY (run_id) REFERENCES trade_run(run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='不可变操作审计';

INSERT IGNORE INTO strategy_definition(code, name, description) VALUES
    ('short_term', '短线', '1-3 个交易日的盘中条件计划'),
    ('medium_term', '中线', '1-4 周趋势与行业强度计划'),
    ('long_term', '长线', '1-3 个月低换手趋势计划');

INSERT IGNORE INTO strategy_version(strategy_code, version_no, algorithm_fingerprint, config_json) VALUES
    ('short_term', 1, 'short-v1', JSON_OBJECT()),
    ('medium_term', 1, 'medium-v1', JSON_OBJECT()),
    ('long_term', 1, 'long-v1', JSON_OBJECT());
