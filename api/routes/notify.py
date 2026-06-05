"""通知配置接口（SMTP，前端录入）+ 测试邮件"""
from fastapi import APIRouter
from pydantic import BaseModel

import notify as notify_mod
from api.errors import BadRequest

router = APIRouter(prefix="/api/notify", tags=["notify"])


class SmtpConfig(BaseModel):
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_pass: str
    mail_to: str
    enabled: bool = True


@router.get("/config")
def get_config():
    """读 SMTP 配置（密码脱敏）"""
    return notify_mod.get_config(mask=True)


@router.put("/config")
def set_config(body: SmtpConfig):
    """设置 SMTP 配置（前端填）。

    提示：smtp_pass 用邮箱的"授权码/应用密码"，不是登录密码。
    QQ邮箱 smtp.qq.com:465(SSL)，163 smtp.163.com:465。
    """
    notify_mod.set_config(body.smtp_host, body.smtp_port, body.smtp_user,
                          body.smtp_pass, body.mail_to, body.enabled)
    return {"ok": True}


@router.post("/test")
def test_mail():
    """发一封测试邮件，验证 SMTP 配置是否可用"""
    r = notify_mod.send_mail(
        "【测试】A 股量化系统邮件通知",
        "<h3 style='color:#27ae60'>✅ 邮件配置成功</h3>"
        "<p>这是一封测试邮件，说明你的 SMTP 配置可用，盘后分析报告会发到这里。</p>")
    if not r.get("sent"):
        raise BadRequest(f"发送失败: {r.get('reason')}", code="MAIL_SEND_FAILED")
    return {"ok": True, "to": r.get("to")}
