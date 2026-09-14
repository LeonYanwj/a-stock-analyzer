-- 第一阶段只读市场扫描默认规则策略。
-- 只新增策略定义和版本，不修改历史策略或交易记录。
INSERT IGNORE INTO strategy_definition(code, name, description) VALUES
  ('trend_momentum', '趋势动量', '收盘价、均线和动量共同确认的只读选股'),
  ('breakout_volume', '突破放量', '突破阶段高点且成交量配合的只读选股'),
  ('low_volatility', '低波动趋势', '正动量、均线向上且波动较低的只读选股');

INSERT IGNORE INTO strategy_version
  (strategy_code, version_no, algorithm_fingerprint, config_json, signal_source, created_at)
VALUES
  ('trend_momentum', 1, 'rule-trend-momentum-v1', JSON_OBJECT('engine', 'rule_scan'), 'vnpy', NOW()),
  ('breakout_volume', 1, 'rule-breakout-volume-v1', JSON_OBJECT('engine', 'rule_scan'), 'vnpy', NOW()),
  ('low_volatility', 1, 'rule-low-volatility-v1', JSON_OBJECT('engine', 'rule_scan'), 'vnpy', NOW());
