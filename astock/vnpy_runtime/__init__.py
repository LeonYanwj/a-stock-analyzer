"""vn.py 运行时适配层。

项目业务代码通过本包访问 vn.py，避免在 FastAPI 路由中散落 gateway 和策略
初始化细节。vn.py/xtquant 是可选运行依赖；未安装时，状态接口仍能明确报告原因。
"""

from .config import VnpySettings
from .gateway import VnpyRuntime

__all__ = ["VnpySettings", "VnpyRuntime"]
