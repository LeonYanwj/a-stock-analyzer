-- 将新交易实例的策略版本切换到项目内 vendored vn.py Alpha101。
-- 本迁移只追加版本记录；已经冻结的 legacy/new 历史实例保持原样可审计。

INSERT IGNORE INTO strategy_version(
    strategy_code, version_no, algorithm_fingerprint, config_json, signal_source
)
VALUES
    ('short_term', 3, 'vnpy-alpha101-short_term-v1',
     JSON_OBJECT('engine', 'Alpha101', 'signal_mode', 'cross_sectional_rank',
                 'profile', 'short_term'), 'vnpy'),
    ('medium_term', 3, 'vnpy-alpha101-medium_term-v1',
     JSON_OBJECT('engine', 'Alpha101', 'signal_mode', 'cross_sectional_rank',
                 'profile', 'medium_term'), 'vnpy'),
    ('long_term', 3, 'vnpy-alpha101-long_term-v1',
     JSON_OBJECT('engine', 'Alpha101', 'signal_mode', 'cross_sectional_rank',
                 'profile', 'long_term'), 'vnpy');
