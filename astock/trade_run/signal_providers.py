"""交易实例信号提供器。

提供器只输出可复现的研究候选，不写交易计划、不修改账务。计划生命周期统一由
``TradeRunPlanner`` 管理，以防 HTTP 路由或旧 ``screen.py`` 绕开截面和审计。
"""
import math
from datetime import timedelta

LEGACY_PROFILE = {"short_term": "short_term", "medium_term": "swing", "long_term": "trend"}


class SignalProviderError(RuntimeError):
    pass


class VnpyAlphaSignalProvider:
    """以 vn.py Alpha101 因子替代旧的项目内多因子选股策略。"""

    source = "vnpy"
    lookback_rows = 320
    candidate_limit = 10

    def __init__(self, repository=None, reference=False):
        self.repository = repository
        self.reference = reference

    def candidates(self, run, as_of, asset_types, progress_callback=None):
        if not self.repository:
            raise SignalProviderError("vn.py Alpha 策略未配置数据仓储")
        rows = []
        if "stock" in asset_types:
            if progress_callback:
                progress_callback(20, "读取 vn.py Alpha101 所需历史日线")
            rows.extend(self._stock_candidates(run, as_of))
        if "etf" in asset_types:
            if progress_callback:
                progress_callback(80, "读取 vn.py Alpha101 所需 ETF 白名单日线")
            rows.extend(self._etf_candidates(run, as_of))
        if progress_callback:
            progress_callback(100, "vn.py Alpha 候选已生成")
        return rows

    def _stock_candidates(self, run, as_of):
        cutoff_date = (as_of.date() - timedelta(days=1)).isoformat()
        sql = (
            "SELECT ts_code, trade_date, open, high, low, close, vol, amount FROM ("
            "SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close, d.vol, d.amount, "
            "ROW_NUMBER() OVER (PARTITION BY d.ts_code ORDER BY d.trade_date DESC) rn "
            "FROM market_daily d JOIN market_stock_basic b ON b.ts_code=d.ts_code "
            "WHERE d.trade_date<=? AND d.adjust=? AND b.is_active=1 AND b.is_st=0 "
            "AND (b.symbol LIKE ? OR b.symbol LIKE ? OR b.symbol LIKE ? OR b.symbol LIKE ? "
            "OR b.symbol LIKE ? OR b.symbol LIKE ? OR b.symbol LIKE ?)"
            ") x WHERE rn<=? ORDER BY trade_date ASC, ts_code ASC"
        )
        params = (cutoff_date, "qfq", "600%", "601%", "603%", "605%", "000%", "001%", "002%",
                  self.lookback_rows)
        try:
            raw = self.repository.conn.execute(sql, params).fetchall()
        except Exception as exc:
            raise SignalProviderError(f"读取 vn.py Alpha 历史日线失败: {type(exc).__name__}: {exc}") from exc
        bars = []
        for item in raw:
            row = dict(item) if not isinstance(item, dict) else item
            bars.append({
                "ts_code": row["ts_code"], "trade_date": row["trade_date"],
                "open": row["open"], "high": row["high"], "low": row["low"],
                "close": row["close"], "volume": row["vol"], "amount": row["amount"],
            })
        try:
            from astock.vnpy_runtime.signals import (
                VnpyAlphaSignalError,
                generate_alpha101_signals,
            )
            signals = generate_alpha101_signals(bars, run["strategy_code"], self.reference)
        except VnpyAlphaSignalError as exc:
            raise SignalProviderError(str(exc)) from exc
        return self._candidate_rows(signals, "stock", as_of)

    def _etf_candidates(self, run, as_of):
        cutoff_date = (as_of.date() - timedelta(days=1)).isoformat()
        sql = (
            "SELECT ts_code, trade_date, open, high, low, close, vol, amount FROM ("
            "SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close, d.vol, d.amount, "
            "ROW_NUMBER() OVER (PARTITION BY d.ts_code ORDER BY d.trade_date DESC) rn "
            "FROM market_etf_daily d JOIN market_etf_basic b ON b.ts_code=d.ts_code "
            "WHERE d.trade_date<=? AND b.listing_status=? AND b.whitelist=?"
            ") x WHERE rn<=? ORDER BY trade_date ASC, ts_code ASC"
        )
        try:
            raw = self.repository.conn.execute(
                sql, (cutoff_date, "active", 1, self.lookback_rows)
            ).fetchall()
        except Exception as exc:
            raise SignalProviderError(f"读取 vn.py Alpha ETF 日线失败: {type(exc).__name__}: {exc}") from exc
        bars = []
        for item in raw:
            row = dict(item) if not isinstance(item, dict) else item
            bars.append({
                "ts_code": row["ts_code"], "trade_date": row["trade_date"],
                "open": row["open"], "high": row["high"], "low": row["low"],
                "close": row["close"], "volume": row["vol"], "amount": row["amount"],
            })
        signals = self._generate_signals(bars, run["strategy_code"])
        return self._candidate_rows(signals, "etf", as_of)

    def _generate_signals(self, bars, strategy_code):
        try:
            from astock.vnpy_runtime.signals import (
                VnpyAlphaSignalError,
                generate_alpha101_signals,
            )
            return generate_alpha101_signals(bars, strategy_code, self.reference)
        except VnpyAlphaSignalError as exc:
            raise SignalProviderError(str(exc)) from exc

    def _candidate_rows(self, signals, asset_type, as_of):
        output = []
        for item in signals[:self.candidate_limit]:
            vt_symbol = item["vt_symbol"]
            ts_code = _to_ts_code(vt_symbol)
            output.append({
                "ts_code": ts_code,
                "asset_type": asset_type,
                "side": "buy",
                "reference_price": item["close"],
                "score": item["signal"],
                "reason": f"vn.py Alpha101 横截面信号入选（{item['factor_count']} 个因子）",
                "data_status": "delayed",
                "data_source": "vnpy_alpha101_reference" if self.reference else "vnpy_alpha101",
                "market_time": as_of,
            })
        return output


def _to_ts_code(vt_symbol):
    symbol, exchange = vt_symbol.rsplit(".", 1)
    return f"{symbol}.{'SH' if exchange == 'SSE' else 'SZ' if exchange == 'SZSE' else exchange}"


class LegacySignalProvider:
    source = "legacy"

    def __init__(self, repository=None):
        self.repository = repository

    def candidates(self, run, as_of, asset_types, progress_callback=None):
        """把旧主板选股结果转换为只读候选；显式传 as_of，避免未来数据泄漏。"""
        rows = []
        if "stock" in asset_types:
            from astock.screen import screen_market
            if progress_callback:
                progress_callback(5, "加载旧体系股票因子")
            result = screen_market(
                strategy=LEGACY_PROFILE[run["strategy_code"]], top_n_arg=10,
                as_of_dt=as_of, verbose=False, persist_ratings=False,
                stock_scope=run.get("stock_scope", "quick"),
                quick_limit=run.get("quick_limit", 100),
                progress_callback=progress_callback,
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
            if progress_callback:
                progress_callback(95, "扫描 ETF 白名单")
            rows.extend(EtfSignalProvider(self.repository).candidates(
                run, as_of, asset_types, progress_callback=progress_callback))
        return rows


class RuleSignalProvider:
    source = "new"

    def __init__(self, repository=None):
        self.repository = repository

    def candidates(self, run, as_of, asset_types, progress_callback=None):
        """首版规则体系：只用 <= as_of 的日线做趋势、动量和流动性过滤。"""
        if not self.repository:
            raise SignalProviderError("新规则体系未配置数据仓储")
        rows = []
        if "stock" in asset_types:
            if progress_callback:
                progress_callback(20, "读取股票日线并计算趋势")
            rows.extend(self._daily_candidates("market_daily", "stock", as_of, 10))
        if "etf" in asset_types:
            if progress_callback:
                progress_callback(70, "读取 ETF 日线与白名单")
            rows.extend(EtfSignalProvider(self.repository).candidates(
                run, as_of, asset_types, progress_callback=progress_callback))
        if progress_callback:
            progress_callback(100, "新规则候选已整理")
        return rows

    def _daily_candidates(self, table, asset_type, as_of, limit):
        # MySQL 8/MariaDB 10.11 的窗口函数；截面条件是防未来数据泄漏的硬边界。
        source = table
        fields = "ts_code, close, amount, pct_chg"
        params = []
        if asset_type == "stock":
            source = (
                "market_daily d JOIN market_stock_basic b ON b.ts_code=d.ts_code "
                "AND b.is_active=1 AND b.is_st=0 AND (b.symbol LIKE ? OR b.symbol LIKE ? "
                "OR b.symbol LIKE ? OR b.symbol LIKE ? OR b.symbol LIKE ? "
                "OR b.symbol LIKE ? OR b.symbol LIKE ?)"
            )
            fields = "d.ts_code, d.close, d.amount, d.pct_chg"
            # 通配符作为绑定值，而不是 SQL 文本的一部分。PyMySQL 会对 SQL 使用
            # `%` 参数化；若直接写 `LIKE '600%'` 会被误当成格式化占位符。
            params.extend(("600%", "601%", "603%", "605%", "000%", "001%", "002%"))
        sql = (
            "SELECT ts_code, close, amount, pct_chg FROM ("
            f"SELECT {fields}, ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) rn "
            f"FROM {source} WHERE trade_date<=?" ") x WHERE rn<=21"
        )
        try:
            # 两个自动窗口都在收盘前，日线只能截止到前一自然日；周末会自然
            # 落到最近一个已存在交易日。这是防止收盘数据回写盘中计划的硬边界。
            cutoff_date = (as_of.date() - timedelta(days=1)).isoformat()
            cur = self.repository.conn.execute(sql, tuple(params + [cutoff_date]))
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

    def candidates(self, run, as_of, asset_types, progress_callback=None):
        if "etf" not in asset_types:
            return []
        if not self.repository:
            # Legacy provider has no repo injection: use no ETF rather than silently invent candidates.
            return []
        if progress_callback:
            progress_callback(80, "计算 ETF 趋势、动量与流动性")
        candidates = self._daily_candidates("market_etf_daily", "etf", as_of, 20)
        try:
            cur = self.repository.conn.execute(
                "SELECT ts_code, name, etf_type, tracking_index, avg_amount FROM market_etf_basic WHERE listing_status='active' AND whitelist=1",
            )
            allowed = {r["ts_code"] if isinstance(r, dict) else r[0]: dict(r) if not isinstance(r, dict) else r for r in cur.fetchall()}
        except Exception as exc:
            raise SignalProviderError(f"读取 ETF 白名单失败: {type(exc).__name__}: {exc}")
        output = [dict(item, reason=f"{item['reason']}；ETF 白名单", etf_meta=allowed[item["ts_code"]])
                  for item in candidates if item["ts_code"] in allowed]
        if progress_callback:
            progress_callback(100, f"ETF 白名单匹配完成：{len(output)} 个候选")
        return output
