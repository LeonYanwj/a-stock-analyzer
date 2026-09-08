"""vn.py Alpha 股票策略入口。"""


def load_equity_demo_strategy():
    """加载 vn.py 官方股票多标的示例策略。

    延迟导入是为了让 API 在未安装 Alpha 可选依赖时仍能启动并报告状态。
    """
    try:
        from vnpy.alpha.strategy.strategies.equity_demo_strategy import (
            EquityDemoStrategy,
        )
    except ImportError as exc:
        raise RuntimeError(f"vn.py Alpha 策略依赖未安装: {exc}") from exc
    return EquityDemoStrategy


def equity_demo_strategy_info() -> dict:
    """返回官方示例策略的可读规则摘要。"""
    return {
        "name": "EquityDemoStrategy",
        "source": "vnpy.alpha.strategy.strategies.equity_demo_strategy",
        "logic": [
            "按模型信号排序多只股票",
            "持有信号排名前 top_k 的股票",
            "卖出指数成分外或排名靠后的持仓",
            "设置最低持有天数",
            "按可用现金平均分配目标仓位",
            "使用 vn.py 目标仓位执行调仓并计入手续费",
        ],
    }
