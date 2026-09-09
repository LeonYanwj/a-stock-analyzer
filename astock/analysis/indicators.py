import pandas as pd
import numpy as np


def MA(close, period):
    """移动平均线"""
    return close.rolling(window=period).mean()


def EMA(close, period):
    """指数移动平均线"""
    return close.ewm(span=period, adjust=False).mean()


def MACD(close, fast=12, slow=26, signal=9):
    """MACD 指标
    Returns:
        dif: 快线 - 慢线
        dea: dif 的信号线
        macd_hist: MACD 柱状图 (dif - dea) * 2
    """
    ema_fast = EMA(close, fast)
    ema_slow = EMA(close, slow)
    dif = ema_fast - ema_slow
    dea = EMA(dif, signal)
    macd_hist = (dif - dea) * 2
    return dif, dea, macd_hist


def RSI(close, period=14):
    """相对强弱指数"""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def KDJ(high, low, close, n=9, m1=3, m2=3):
    """KDJ 随机指标
    Returns:
        k, d, j
    """
    lowest_low = low.rolling(window=n).min()
    highest_high = high.rolling(window=n).max()
    rsv = (close - lowest_low) / (highest_high - lowest_low) * 100
    rsv = rsv.fillna(50)

    k = rsv.ewm(com=m1 - 1, adjust=False).mean()
    d = k.ewm(com=m2 - 1, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def BOLL(close, period=20, std_dev=2):
    """布林带
    Returns:
        upper: 上轨
        middle: 中轨（MA）
        lower: 下轨
    """
    middle = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


def add_all_indicators(df):
    """为 DataFrame 添加所有技术指标
    Args:
        df: 需包含 open, high, low, close, vol 列
    Returns:
        添加指标后的 DataFrame
    """
    df = df.copy()

    # 均线
    for p in [5, 10, 20, 60]:
        df[f"ma{p}"] = MA(df["close"], p)

    # MACD
    df["dif"], df["dea"], df["macd"] = MACD(df["close"])

    # RSI
    df["rsi6"] = RSI(df["close"], 6)
    df["rsi12"] = RSI(df["close"], 12)
    df["rsi24"] = RSI(df["close"], 24)

    # KDJ
    df["k"], df["d"], df["j"] = KDJ(df["high"], df["low"], df["close"])

    # 布林带
    df["boll_upper"], df["boll_mid"], df["boll_lower"] = BOLL(df["close"])

    return df
