# 复制本文件为 config.py 并填入你自己的 Tushare token
# config.py 已被 .gitignore 忽略，不会被提交

# Tushare API 配置
# 在 https://tushare.pro 注册并获取 token
TUSHARE_TOKEN = "YOUR_TUSHARE_TOKEN_HERE"

# 数据缓存目录
CACHE_DIR = "cache"

# 回测默认参数
DEFAULT_COMMISSION = 0.0001       # 手续费率 万一
DEFAULT_MIN_COMMISSION = 5.0      # 单笔最低佣金 5 元（不免 5）
DEFAULT_SLIPPAGE = 0.001          # 滑点 0.1%
DEFAULT_INITIAL_CAPITAL = 40000   # 初始资金 4 万
