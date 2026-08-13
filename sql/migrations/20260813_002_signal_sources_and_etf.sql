-- 交易实例“人工实盘验证版”增量结构。
-- 前置：20260813_001_trade_run_foundation 已执行。
-- 本迁移只新增字段、索引和表；不修改 paper_*、行情历史或回测记录。

ALTER TABLE strategy_version
    ADD COLUMN signal_source VARCHAR(16) NOT NULL DEFAULT 'legacy' AFTER config_json,
    ADD INDEX idx_strategy_version_source (strategy_code, signal_source, version_no);

ALTER TABLE trade_run
    ADD COLUMN primary_signal_source VARCHAR(16) NOT NULL DEFAULT 'legacy' AFTER frozen_config_json,
    ADD COLUMN shadow_signal_source VARCHAR(16) NOT NULL DEFAULT 'new' AFTER primary_signal_source,
    ADD COLUMN plan_windows_json JSON NULL AFTER shadow_signal_source;

UPDATE trade_run
SET plan_windows_json = JSON_ARRAY('pre_market', 'midday')
WHERE plan_windows_json IS NULL;

ALTER TABLE trade_run
    MODIFY COLUMN plan_windows_json JSON NOT NULL;

ALTER TABLE signal_plan
    ADD COLUMN signal_source VARCHAR(16) NOT NULL DEFAULT 'legacy' AFTER evidence_json,
    ADD COLUMN plan_window VARCHAR(16) NOT NULL DEFAULT 'manual' AFTER signal_source,
    ADD COLUMN as_of DATETIME NULL AFTER plan_window,
    ADD COLUMN observation_id BIGINT NULL AFTER as_of,
    ADD COLUMN execution_confirmation_required TINYINT NOT NULL DEFAULT 0 AFTER observation_id,
    ADD INDEX idx_plan_run_source_window (run_id, signal_source, plan_window, as_of),
    ADD INDEX idx_plan_observation (observation_id),
    ADD CONSTRAINT fk_plan_observation FOREIGN KEY (observation_id) REFERENCES market_data_observation(observation_id);

ALTER TABLE execution_fill
    ADD COLUMN broker_quote_confirmed TINYINT NOT NULL DEFAULT 0 AFTER note,
    ADD COLUMN quote_checked_at DATETIME NULL AFTER broker_quote_confirmed;

CREATE TABLE plan_comparison (
    comparison_id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id                  BIGINT NOT NULL,
    plan_date               DATE NOT NULL,
    plan_window             VARCHAR(16) NOT NULL,
    ts_code                 VARCHAR(16) NOT NULL,
    side                    VARCHAR(8) NOT NULL,
    comparison_type         VARCHAR(16) NOT NULL COMMENT 'overlap/primary_only/shadow_only',
    primary_plan_id         BIGINT NULL,
    shadow_plan_id          BIGINT NULL,
    mirrored_fill_id        BIGINT NULL COMMENT '仅 overlap 使用主计划真实成交镜像',
    opportunity_note        VARCHAR(500) NULL,
    created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_comparison_window_symbol (run_id, plan_date, plan_window, ts_code, side),
    INDEX idx_comparison_run_type (run_id, comparison_type, created_at),
    CONSTRAINT fk_comparison_run FOREIGN KEY (run_id) REFERENCES trade_run(run_id),
    CONSTRAINT fk_comparison_primary_plan FOREIGN KEY (primary_plan_id) REFERENCES signal_plan(plan_id),
    CONSTRAINT fk_comparison_shadow_plan FOREIGN KEY (shadow_plan_id) REFERENCES signal_plan(plan_id),
    CONSTRAINT fk_comparison_fill FOREIGN KEY (mirrored_fill_id) REFERENCES execution_fill(fill_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='主影子计划比较，不改变真实账务';

CREATE TABLE plan_generation (
    run_id                  BIGINT NOT NULL,
    plan_window             VARCHAR(16) NOT NULL,
    plan_date               DATE NOT NULL,
    status                  VARCHAR(16) NOT NULL COMMENT 'processing/generated/failed',
    started_at              DATETIME NOT NULL,
    completed_at            DATETIME NULL,
    error_message           VARCHAR(500) NULL,
    PRIMARY KEY (run_id, plan_window, plan_date),
    CONSTRAINT fk_generation_run FOREIGN KEY (run_id) REFERENCES trade_run(run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='计划窗口数据库任务锁与幂等记录';

CREATE TABLE market_etf_basic (
    ts_code                 VARCHAR(16) PRIMARY KEY,
    symbol                  VARCHAR(10) NOT NULL,
    name                    VARCHAR(64) NOT NULL,
    etf_type                VARCHAR(32) NULL,
    tracking_index          VARCHAR(128) NULL,
    listing_status          VARCHAR(16) NOT NULL DEFAULT 'active',
    whitelist               TINYINT NOT NULL DEFAULT 0,
    avg_amount              DECIMAL(20,2) NULL,
    updated_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_etf_pool (listing_status, whitelist, avg_amount)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ETF 基础信息与首版高流动性白名单';

CREATE TABLE market_etf_daily (
    ts_code                 VARCHAR(16) NOT NULL,
    trade_date              DATE NOT NULL,
    open                    DECIMAL(16,4) NULL,
    high                    DECIMAL(16,4) NULL,
    low                     DECIMAL(16,4) NULL,
    close                   DECIMAL(16,4) NULL,
    vol                     DECIMAL(20,2) NULL,
    amount                  DECIMAL(20,2) NULL,
    pct_chg                 DECIMAL(10,4) NULL,
    PRIMARY KEY (ts_code, trade_date),
    INDEX idx_etf_daily_date (trade_date, ts_code),
    CONSTRAINT fk_etf_daily_basic FOREIGN KEY (ts_code) REFERENCES market_etf_basic(ts_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ETF 未复权研究日线';

-- 新规则体系版本不可覆盖旧版本；后续发布只能追加更高 version_no。
INSERT IGNORE INTO strategy_version(strategy_code, version_no, algorithm_fingerprint, config_json, signal_source)
VALUES
    ('short_term', 2, 'rule-short_term-v1', JSON_OBJECT('profile', 'volume_price_breakout'), 'new'),
    ('medium_term', 2, 'rule-medium_term-v1', JSON_OBJECT('profile', 'trend_sector_volatility'), 'new'),
    ('long_term', 2, 'rule-long_term-v1', JSON_OBJECT('profile', 'low_vol_quality_value'), 'new');
