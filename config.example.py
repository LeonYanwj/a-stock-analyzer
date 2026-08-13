# 复制本文件为 config.py 并填入你自己的 Tushare token
# config.py 已被 .gitignore 忽略，不会被提交

# Tushare API 配置
# 在 https://tushare.pro 注册并获取 token
TUSHARE_TOKEN = "YOUR_TUSHARE_TOKEN_HERE"

# 数据缓存目录
CACHE_DIR = "cache"

# MySQL 数据库配置（复制本文件为 config.py 并填写真实密码）
DB_HOST     = "127.0.0.1"
DB_PORT     = 3306
DB_USER     = "quant"
DB_PASSWORD = "YOUR_DB_PASSWORD_HERE"
DB_NAME     = "quant_data"

# 新交易实例接口的访问密钥。部署时请改为强随机值，或改由环境变量
# TRADE_RUN_API_KEY 提供；不要提交真实密钥。
TRADE_RUN_API_KEY = "CHANGE_ME_TO_A_RANDOM_SECRET"

# 管理后台会话签名密钥；部署时设置为与 API Key 不同的随机值。
# 可用环境变量 TRADE_RUN_SESSION_SECRET 覆盖；不要提交真实密钥。
TRADE_RUN_SESSION_SECRET = "CHANGE_ME_TO_ANOTHER_RANDOM_SECRET"

# 生产 HTTPS 站点设为 True，本地 Vite 联调保留 False。
TRADE_RUN_COOKIE_SECURE = False

# 回测默认参数
DEFAULT_COMMISSION = 0.0001       # 手续费率 万一
DEFAULT_MIN_COMMISSION = 5.0      # 单笔最低佣金 5 元（不免 5）
DEFAULT_SLIPPAGE = 0.001          # 滑点 0.1%
DEFAULT_INITIAL_CAPITAL = 40000   # 初始资金 4 万
