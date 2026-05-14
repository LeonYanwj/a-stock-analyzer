import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd

matplotlib.rcParams["font.sans-serif"] = ["SimHei"]
matplotlib.rcParams["axes.unicode_minus"] = False


def plot_kline_with_indicators(df, title="K线与技术指标"):
    """绘制蜡烛图 + 均线 + BOLL + MACD + RSI"""
    data = df.copy()
    data["trade_date"] = pd.to_datetime(data["trade_date"])
    data = data.set_index("trade_date")
    data = data.rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "vol": "Volume",
    })

    addplots = []
    for col, color in [("ma5", "orange"), ("ma10", "blue"),
                       ("ma20", "purple"), ("ma60", "green")]:
        if col in data.columns:
            addplots.append(mpf.make_addplot(data[col], color=color, width=0.8))

    if "boll_upper" in data.columns:
        addplots.append(mpf.make_addplot(data["boll_upper"], color="gray",
                                         linestyle="--", width=0.6))
        addplots.append(mpf.make_addplot(data["boll_lower"], color="gray",
                                         linestyle="--", width=0.6))

    if "dif" in data.columns:
        addplots.append(mpf.make_addplot(data["dif"], panel=2, color="blue",
                                         width=0.8, ylabel="MACD"))
        addplots.append(mpf.make_addplot(data["dea"], panel=2, color="orange",
                                         width=0.8))
        macd_colors = ["red" if v >= 0 else "green" for v in data["macd"]]
        addplots.append(mpf.make_addplot(data["macd"], panel=2, type="bar",
                                         color=macd_colors, alpha=0.6))

    if "rsi6" in data.columns:
        addplots.append(mpf.make_addplot(data["rsi6"], panel=3, color="blue",
                                         width=0.8, ylabel="RSI"))
        addplots.append(mpf.make_addplot(data["rsi12"], panel=3, color="orange",
                                         width=0.8))

    # A 股配色：阳线红、阴线绿
    mc = mpf.make_marketcolors(up="red", down="green",
                               edge="inherit", wick="inherit", volume="inherit")
    style = mpf.make_mpf_style(base_mpf_style="charles", marketcolors=mc,
                               rc={"font.sans-serif": ["SimHei"],
                                   "axes.unicode_minus": False})

    mpf.plot(
        data,
        type="candle",
        style=style,
        addplot=addplots,
        volume=True,
        volume_panel=1,
        panel_ratios=(4, 1, 2, 1),
        figsize=(14, 12),
        title=title,
        savefig=dict(fname="kline_indicators.png", dpi=150, bbox_inches="tight"),
        warn_too_much_data=10000,
    )


def plot_backtest_result(portfolio_df, title="回测净值曲线"):
    """绘制回测净值曲线"""
    fig, ax = plt.subplots(figsize=(12, 5))

    ax.plot(portfolio_df["date"], portfolio_df["total_value"],
            label="组合净值", color="blue", linewidth=1.2)
    ax.fill_between(portfolio_df["date"], portfolio_df["total_value"],
                    portfolio_df["total_value"].iloc[0], alpha=0.1, color="blue")
    ax.axhline(y=portfolio_df["total_value"].iloc[0], color="gray",
               linestyle="--", alpha=0.5, label="初始资金")
    ax.set_title(title)
    ax.set_xlabel("日期")
    ax.set_ylabel("组合价值（元）")
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig("backtest_result.png", dpi=150, bbox_inches="tight")
    plt.close()
