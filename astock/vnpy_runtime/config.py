from __future__ import annotations

"""vn.py/迅投配置。

所有配置统一从项目 ``config.py`` 读取。Token 只在进程内使用，禁止写入日志、
API 响应或 Git。``config.py`` 已被忽略，示例字段见 ``config.example.py``。
"""
import importlib
from dataclasses import dataclass


@dataclass(frozen=True)
class VnpySettings:
    """运行时所需的非敏感配置和一个不回显的 Token。"""

    xt_token: str | None
    xt_mode: str = "token"
    xt_account_id: str | None = None
    xt_account_type: str = "股票"
    xt_path: str | None = None

    @classmethod
    def from_config(cls, config_module=None) -> "VnpySettings":
        """从项目配置模块读取 vn.py 设置，不读取环境变量。"""
        config = config_module or importlib.import_module("config")
        mode = str(getattr(config, "XTPY_MODE", "token")).strip().lower()
        if mode not in {"token", "client"}:
            raise ValueError("XTPY_MODE 仅支持 token 或 client")
        token = getattr(config, "XT_TOKEN", None)
        return cls(
            xt_token=token.strip() if token and token.strip() else None,
            xt_mode=mode,
            xt_account_id=_optional_value(getattr(config, "XT_ACCOUNT_ID", None)),
            xt_account_type=str(getattr(config, "XT_ACCOUNT_TYPE", "股票")).strip() or "股票",
            xt_path=_optional_value(getattr(config, "XT_PATH", None)),
        )

    @property
    def configured(self) -> bool:
        """是否具备启动迅投行情服务的必要配置。"""
        if self.xt_mode == "client":
            return bool(self.xt_path)
        return bool(self.xt_token)

    def public_dict(self) -> dict:
        """返回可安全给状态接口使用的配置摘要，不返回 Token。"""
        return {
            "configured": self.configured,
            "mode": self.xt_mode,
            "account_configured": bool(self.xt_account_id),
            "account_type": self.xt_account_type,
            "token_configured": bool(self.xt_token),
        }


def _optional_value(value) -> str | None:
    return value.strip() if value and value.strip() else None
