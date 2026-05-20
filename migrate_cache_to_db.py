"""把现有 cache/*.csv 迁移到 MySQL（market_daily 表）

只迁移 daily_*.csv（日线数据），其他类型先跳过。
重复迁移安全（UPSERT 自动覆盖）。

用法:
    python migrate_cache_to_db.py
    python migrate_cache_to_db.py --dry-run    # 只看要迁移多少不实际写入
"""
import sys
import io
import os
import glob
import argparse
import time

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import pandas as pd

from config import CACHE_DIR
from data.db import get_conn, upsert_daily


# 数值列规范化
_NUM_COLS = ["open", "high", "low", "close", "vol", "amount", "pct_chg", "turnover_rate"]


def _parse_cache_file(path: str) -> pd.DataFrame:
    """读一个 daily cache csv，返回标准化 DataFrame"""
    df = pd.read_csv(path)
    if "ts_code" not in df.columns or "trade_date" not in df.columns:
        return pd.DataFrame()
    # 强制转字符串再解析（避免 pandas 把数字 20240102 当 Unix 纳秒）
    df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str), errors="coerce")
    df = df.dropna(subset=["trade_date"])
    df["trade_date"] = df["trade_date"].dt.strftime("%Y-%m-%d")
    for c in _NUM_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    keep = [c for c in ["ts_code", "trade_date"] + _NUM_COLS if c in df.columns]
    return df[keep]


def main():
    parser = argparse.ArgumentParser(description="迁移 cache 到 MySQL")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    pattern = os.path.join(CACHE_DIR, "daily_*.csv")
    files = sorted(glob.glob(pattern))
    print(f"找到 {len(files)} 个 daily cache 文件")

    if args.dry_run:
        # 统计每个文件行数
        total_rows = 0
        for p in files[:5]:
            df = _parse_cache_file(p)
            print(f"  {os.path.basename(p)}: {len(df)} 行")
            total_rows += len(df)
        print(f"\n前 5 个文件共 {total_rows} 行（实际可能更多）")
        print("加上 --dry-run 不会写入。去掉 --dry-run 开始真正迁移。")
        return

    t0 = time.time()
    total_rows = 0
    total_inserted = 0
    failed = 0

    with get_conn() as conn:
        for i, p in enumerate(files, 1):
            try:
                df = _parse_cache_file(p)
                if df.empty:
                    continue
                # 推断复权方式（文件名带 _qfq / _hfq / _raw 后缀）
                fname = os.path.basename(p)
                if "_qfq" in fname:
                    adjust = "qfq"
                elif "_hfq" in fname:
                    adjust = "hfq"
                else:
                    adjust = "qfq"   # 默认前复权（兼容旧 Tushare cache）
                affected = upsert_daily(conn, df, adjust=adjust)
                total_rows += len(df)
                total_inserted += affected
            except Exception as e:
                failed += 1
                print(f"  [fail] {os.path.basename(p)}: {type(e).__name__}: {str(e)[:80]}")
            if i % 50 == 0 or i == len(files):
                elapsed = time.time() - t0
                print(f"  [{i}/{len(files)}] 已迁移 {total_rows} 行, "
                      f"affected {total_inserted}, 失败 {failed}, "
                      f"耗时 {elapsed:.0f}s")

    print(f"\n[完成] 共扫 {len(files)} 个文件, 总行数 {total_rows}, "
          f"affected {total_inserted}, 失败 {failed}")
    print(f"耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
