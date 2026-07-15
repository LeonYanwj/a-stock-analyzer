"""每日评级对应的纯业务规则，不访问数据库。"""


def rating_exit_reason(current, previous=None, eligibility=None):
    """返回评级退出原因；None 表示继续持有。"""
    if eligibility is not None and not eligibility.get("eligible", True):
        flags = eligibility.get("financial_risk_flags") or []
        return ",".join(flags) or "HARD_RISK"
    if current is None:
        return None
    if current.get("financial_risk_level") == "high":
        return "FINANCIAL_RISK"
    if current.get("grade") == "D":
        return "GRADE_D"
    previous_state = previous.get("trend_state") if previous else None
    if (current.get("trend_state") in ("weak", "bad")
            and previous_state in ("weak", "bad")):
        return "TREND_WEAK_2D"
    return None
