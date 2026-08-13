"""交易实例信号提供器。

提供器只输出可复现的研究候选，不写交易计划、不修改账务。计划生命周期统一由
``TradeRunPlanner`` 管理，以防 HTTP 路由或旧 ``screen.py`` 绕开截面和审计。
"""
import math
from datetime import timedelta


LEGACY_PROFILE = {"short_term": "short_term", "medium_term": "swing", "long_term": "trend"}


class SignalProviderError(RuntimeError):
    pass


class LegacySignalProvider:
    source = "legacy"

    def __init__(self, repository=None):
        self.repository = repository

    def candidates(self, run, as_of, asset_types):
        """把旧主板选股结果转换为只读候选；显式传 as_of，避免未来数据泄漏。"""
        rows = []
        if "stock" in asset_types:
            from screen import screen_market
            result = screen_market(
                strategy=LEGACY_PROFILE[run["strategy_code"]], top_n_arg=10,
                as_of_dt=as_of, verbose=False, persist_ratings=False,
            )
            for ts_code, item in result.iterrows():
                price = float(item.get("close") or item.get("last_close") or 0)
                if not math.isfinite(price) or price <= 0:
                    continue
                rows.append({"ts_code": ts_code, "asset_type": "stock", "side": "buy",
                             "reference_price": price, "score": float(item.get("score", 0)),
                             "reason": "旧体系多因子排名入选", "data_status": "delayed",
                             "data_source": "legacy_screen", "market_time": as_of})
        if "etf" in asset_types:
            rows.extend(EtfSignalProvider(self.repository).candidates(run, as_of, asset_types))
        return rows


class RuleSignalProvider:
    source = "new"

    def __init__(self, repository=None):
        self.repository = repository

    def candidates(self, run, as_of, asset_types):
        """首版规则体系：只用 <= as_of 的日线做趋势、动量和流动性过滤。"""
        if not self.repository:
            raise SignalProviderError("新规则体系未配置数据仓储")
        rows = []
        if "stock" in asset_types:
            rows.extend(self._daily_candidates("market_daily", "stock", as_of, 10))
        if "etf" in asset_types:
            rows.extend(EtfSignalProvider(self.repository).candidates(run, as_of, asset_types))
        return rows

    def _daily_candidates(self, table, asset_type, as_of, limit):
        # MySQL 8/MariaDB 10.11 的窗口函数；截面条件是防未来数据泄漏的硬边界。
        source = table
        fields = "ts_code, close, amount, pct_chg"
        if asset_type == "stock":
            source = (
                "market_daily d JOIN market_stock_basic b ON b.ts_code=d.ts_code "
                "AND b.is_active=1 AND b.is_st=0 AND (b.symbol LIKE '600%' OR b.symbol LIKE '601%' "
                "OR b.symbol LIKE '603%' OR b.symbol LIKE '605%' OR b.symbol LIKE '000%' "
                "OR b.symbol LIKE '001%' OR b.symbol LIKE '002%')"
            )
            fields = "d.ts_code, d.close, d.amount, d.pct_chg"
        sql = (
            "SELECT ts_code, close, amount, pct_chg FROM ("
            f"SELECT {fields}, ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) rn "
            f"FROM {source} WHERE trade_date<=?" ") x WHERE rn<=21"
        )
        try:
            # 两个自动窗口都在收盘前，日线只能截止到前一自然日；周末会自然
            # 落到最近一个已存在交易日。这是防止收盘数据回写盘中计划的硬边界。
            cutoff_date = (as_of.date() - timedelta(days=1)).isoformat()
            cur = self.repository.conn.execute(sql, (cutoff_date,))
            raw = cur.fetchall()
        except Exception as exc:
            raise SignalProviderError(f"读取 {table} 失败: {type(exc).__name__}: {exc}")
        grouped = {}
        for row in raw:
            row = dict(row) if not isinstance(row, dict) else row
            grouped.setdefault(row["ts_code"], []).append(row)
        out = []
        for ts_code, items in grouped.items():
            if len(items) < 21 or not items[0].get("close") or not items[-1].get("close"):
                continue
            reference = float(items[0]["close"])
            if not math.isfinite(reference) or reference <= 0:
                continue
            momentum = reference / float(items[-1]["close"]) - 1
            avg_amount = sum(float(x.get("amount") or 0) for x in items) / len(items)
            if momentum <= 0 or avg_amount <= 0:
                continue
            out.append({"ts_code": ts_code, "asset_type": asset_type, "side": "buy",
                        "reference_price": reference, "score": momentum,
                        "reason": "新规则：21 日趋势为正且日线流动性有效",
                        "data_status": "delayed", "data_source": table,
                        "market_time": as_of, "avg_amount": avg_amount})
        return sorted(out, key=lambda x: x["score"], reverse=True)[:limit]


class EtfSignalProvider(RuleSignalProvider):
    source = "etf"

    def candidates(self, run, as_of, asset_types):
        if "etf" not in asset_types:
            return []
        if not self.repository:
            # Legacy provider has no repo injection: use no ETF rather than silently invent candidates.
            return []
        candidates = self._daily_candidates("market_etf_daily", "etf", as_of, 20)
        try:
            cur = self.repository.conn.execute(
                "SELECT ts_code, name, etf_type, tracking_index, avg_amount FROM market_etf_basic WHERE listing_status='active' AND whitelist=1",
            )
            allowed = {r["ts_code"] if isinstance(r, dict) else r[0]: dict(r) if not isinstance(r, dict) else r for r in cur.fetchall()}
        except Exception as exc:
            raise SignalProviderError(f"读取 ETF 白名单失败: {type(exc).__name__}: {exc}")
        return [dict(item, reason=f"{item['reason']}；ETF 白名单", etf_meta=allowed[item["ts_code"]])
                for item in candidates if item["ts_code"] in allowed]
