"""邮件通知（SMTP）。

SMTP 配置存 DB 单行表 notify_config，由前端通过 /api/notify/config 录入，
不写死在 config.py。表在导入时自动建。
"""
import smtplib
from email.mime.text import MIMEText
from email.header import Header

from data.db import get_conn


_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS notify_config (
    id         INT PRIMARY KEY,
    smtp_host  VARCHAR(128),
    smtp_port  INT,
    smtp_user  VARCHAR(128),
    smtp_pass  VARCHAR(255),
    mail_to    VARCHAR(255),
    enabled    TINYINT DEFAULT 1,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) CHARACTER SET utf8mb4
"""


def ensure_table():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(_TABLE_SQL)
        cur.close()


def get_config(mask: bool = True) -> dict:
    """读 SMTP 配置。mask=True 时密码脱敏（给前端展示用）。"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT smtp_host, smtp_port, smtp_user, smtp_pass, mail_to, enabled "
                    "FROM notify_config WHERE id=1")
        row = cur.fetchone()
        cur.close()
    if not row:
        return {"configured": False}
    cfg = {
        "configured": True,
        "smtp_host": row[0], "smtp_port": row[1], "smtp_user": row[2],
        "smtp_pass": row[3], "mail_to": row[4], "enabled": bool(row[5]),
    }
    if mask and cfg.get("smtp_pass"):
        cfg["smtp_pass"] = "******"
    return cfg


def set_config(smtp_host, smtp_port, smtp_user, smtp_pass, mail_to, enabled=True):
    """写入/更新 SMTP 配置（单行 id=1）。"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO notify_config (id, smtp_host, smtp_port, smtp_user, smtp_pass, mail_to, enabled) "
            "VALUES (1,%s,%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE smtp_host=VALUES(smtp_host), smtp_port=VALUES(smtp_port), "
            "smtp_user=VALUES(smtp_user), smtp_pass=VALUES(smtp_pass), "
            "mail_to=VALUES(mail_to), enabled=VALUES(enabled)",
            (smtp_host, int(smtp_port), smtp_user, smtp_pass, mail_to, 1 if enabled else 0))
        cur.close()


def send_mail(subject: str, html_body: str, to: str = None) -> dict:
    """用 DB 里的 SMTP 配置发 HTML 邮件。

    Returns: {sent: bool, to?, reason?}
      reason=SMTP_NOT_CONFIGURED / DISABLED / <异常信息>
    """
    cfg = get_config(mask=False)
    if not cfg.get("configured"):
        return {"sent": False, "reason": "SMTP_NOT_CONFIGURED"}
    if not cfg.get("enabled"):
        return {"sent": False, "reason": "DISABLED"}
    to = to or cfg.get("mail_to")
    if not to:
        return {"sent": False, "reason": "NO_RECIPIENT"}

    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = cfg["smtp_user"]
    msg["To"] = to
    port = int(cfg.get("smtp_port") or 465)
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(cfg["smtp_host"], port, timeout=20)
        else:
            server = smtplib.SMTP(cfg["smtp_host"], port, timeout=20)
            server.starttls()
        server.login(cfg["smtp_user"], cfg["smtp_pass"])
        server.sendmail(cfg["smtp_user"], [to], msg.as_string())
        server.quit()
        return {"sent": True, "to": to}
    except Exception as e:
        return {"sent": False, "reason": f"{type(e).__name__}: {e}"}


# 模块导入即确保表存在（DB 不可用时静默）
try:
    ensure_table()
except Exception:
    pass
