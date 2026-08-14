"""交易实例持久化层。

`SqliteTradeRunRepository` 用于隔离测试和本地演示；生产 MySQL 使用同一套
字段定义（见 sql/trade_run_schema.sql），通过 DB-API 连接初始化本仓储即可。
所有会改变账务事实的方法均要求调用方处于 `transaction()` 中。
"""
import contextlib
import json
import sqlite3
import threading
from datetime import datetime


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_definition (
  code TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_version (
  version_id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_code TEXT NOT NULL,
  version_no INTEGER NOT NULL, algorithm_fingerprint TEXT NOT NULL,
  config_json TEXT NOT NULL, signal_source TEXT NOT NULL DEFAULT 'legacy', created_at TEXT NOT NULL,
  UNIQUE(strategy_code, version_no)
);
CREATE TABLE IF NOT EXISTS trade_run (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
  strategy_code TEXT NOT NULL, strategy_version_id INTEGER NOT NULL,
  status TEXT NOT NULL, active_strategy_code TEXT UNIQUE,
  initial_capital REAL NOT NULL, current_cash REAL NOT NULL,
  max_position_pct REAL NOT NULL, asset_types_json TEXT NOT NULL,
  frozen_config_json TEXT NOT NULL, primary_signal_source TEXT NOT NULL DEFAULT 'legacy',
  shadow_signal_source TEXT NOT NULL DEFAULT 'new', plan_windows_json TEXT NOT NULL DEFAULT '["pre_market","midday"]',
  created_at TEXT NOT NULL,
  started_at TEXT, paused_at TEXT, ended_at TEXT, deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS signal_plan (
  plan_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL,
  ts_code TEXT NOT NULL, asset_type TEXT NOT NULL, side TEXT NOT NULL,
  suggested_qty INTEGER NOT NULL, reference_price REAL NOT NULL,
  min_price REAL, max_price REAL, status TEXT NOT NULL, data_status TEXT NOT NULL,
  blocked_reason TEXT, valid_from TEXT, expires_at TEXT, filled_qty INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL,
  evidence_json TEXT NOT NULL, signal_source TEXT NOT NULL DEFAULT 'legacy', plan_window TEXT NOT NULL DEFAULT 'manual',
  as_of TEXT, observation_id INTEGER, execution_confirmation_required INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_fill (
  fill_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL,
  plan_id INTEGER, idempotency_key TEXT NOT NULL UNIQUE, ts_code TEXT NOT NULL,
  side TEXT NOT NULL, qty INTEGER NOT NULL, price REAL NOT NULL, fee REAL NOT NULL,
  executed_at TEXT NOT NULL, source TEXT NOT NULL, note TEXT, broker_quote_confirmed INTEGER NOT NULL DEFAULT 0,
  quote_checked_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_comparison (
  comparison_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, plan_date TEXT NOT NULL, plan_window TEXT NOT NULL,
  ts_code TEXT NOT NULL, side TEXT NOT NULL, comparison_type TEXT NOT NULL,
  primary_plan_id INTEGER, shadow_plan_id INTEGER, mirrored_fill_id INTEGER,
  opportunity_note TEXT, created_at TEXT NOT NULL,
  UNIQUE(run_id, plan_date, plan_window, ts_code, side)
);
CREATE TABLE IF NOT EXISTS plan_generation (
  run_id INTEGER NOT NULL, plan_window TEXT NOT NULL, plan_date TEXT NOT NULL,
  status TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT, error_message TEXT,
  PRIMARY KEY(run_id, plan_window, plan_date)
);
CREATE TABLE IF NOT EXISTS market_data_observation (
  observation_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, ts_code TEXT NOT NULL,
  source TEXT NOT NULL, market_time TEXT, received_at TEXT NOT NULL, delay_seconds INTEGER,
  completeness TEXT NOT NULL, snapshot_ref TEXT, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS market_etf_basic (
  ts_code TEXT PRIMARY KEY, symbol TEXT NOT NULL, name TEXT NOT NULL, etf_type TEXT,
  tracking_index TEXT, listing_status TEXT NOT NULL DEFAULT 'active', whitelist INTEGER NOT NULL DEFAULT 0,
  avg_amount REAL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS market_etf_daily (
  ts_code TEXT NOT NULL, trade_date TEXT NOT NULL, open REAL, high REAL, low REAL, close REAL,
  vol REAL, amount REAL, pct_chg REAL, PRIMARY KEY(ts_code, trade_date)
);
CREATE TABLE IF NOT EXISTS run_position (
  run_id INTEGER NOT NULL, ts_code TEXT NOT NULL, asset_type TEXT NOT NULL,
  qty INTEGER NOT NULL, sellable_qty INTEGER NOT NULL, avg_cost REAL NOT NULL,
  realized_pnl REAL NOT NULL DEFAULT 0, open_date TEXT NOT NULL,
  updated_at TEXT NOT NULL, PRIMARY KEY(run_id, ts_code)
);
CREATE TABLE IF NOT EXISTS run_cash_ledger (
  ledger_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL,
  fill_id INTEGER, entry_type TEXT NOT NULL, amount REAL NOT NULL,
  balance_after REAL NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_event (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL,
  event_type TEXT NOT NULL, message TEXT NOT NULL, payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS risk_event (
  risk_event_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, event_code TEXT NOT NULL,
  severity TEXT NOT NULL, message TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


class SqliteTradeRunRepository:
    def __init__(self, connection=None):
        self.conn = connection or sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def initialize(self):
        self.conn.executescript(SQLITE_SCHEMA)
        now = self.now()
        seeds = [
            ("short_term", "短线", "1-3 个交易日的盘中条件计划", "short-v1"),
            ("medium_term", "中线", "1-4 周趋势与行业强度计划", "medium-v1"),
            ("long_term", "长线", "1-3 个月低换手趋势计划", "long-v1"),
        ]
        for code, name, desc, fingerprint in seeds:
            self.conn.execute("INSERT OR IGNORE INTO strategy_definition VALUES (?,?,?)", (code, name, desc))
            self.conn.execute(
                "INSERT OR IGNORE INTO strategy_version(strategy_code,version_no,algorithm_fingerprint,config_json,signal_source,created_at) VALUES (?,?,?,?,?,?)",
                (code, 1, fingerprint, "{}", "legacy", now),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO strategy_version(strategy_code,version_no,algorithm_fingerprint,config_json,signal_source,created_at) VALUES (?,?,?,?,?,?)",
                (code, 2, f"rule-{code}-v1", "{}", "new", now),
            )
        self.conn.commit()

    @staticmethod
    def now():
        return datetime.now().replace(microsecond=0).isoformat(sep=" ")

    @contextlib.contextmanager
    def transaction(self):
        try:
            self.conn.execute("BEGIN")
            yield self
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    @staticmethod
    def _one(cur):
        row = cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    def _many(cur):
        return [dict(row) for row in cur.fetchall()]

    def latest_strategy_version(self, code, signal_source=None):
        sql = "SELECT * FROM strategy_version WHERE strategy_code=?"
        params = [code]
        if signal_source:
            sql += " AND signal_source=?"
            params.append(signal_source)
        return self._one(self.conn.execute(sql + " ORDER BY version_no DESC LIMIT 1", params))

    def list_strategies(self):
        return self._many(self.conn.execute("SELECT d.code,d.name,d.description,v.version_id,v.version_no,v.algorithm_fingerprint FROM strategy_definition d JOIN strategy_version v ON v.strategy_code=d.code WHERE v.version_no=(SELECT MAX(version_no) FROM strategy_version WHERE strategy_code=d.code) ORDER BY d.code"))

    def list_versions(self, code):
        return self._many(self.conn.execute("SELECT * FROM strategy_version WHERE strategy_code=? ORDER BY version_no DESC", (code,)))

    def insert_run(self, values):
        cur = self.conn.execute("INSERT INTO trade_run(name,strategy_code,strategy_version_id,status,initial_capital,current_cash,max_position_pct,asset_types_json,frozen_config_json,primary_signal_source,shadow_signal_source,plan_windows_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
        return cur.lastrowid

    def get_run(self, run_id, include_deleted=True, for_update=False):
        sql = "SELECT * FROM trade_run WHERE run_id=?"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        # SQLite 没有 FOR UPDATE；事务本身已锁住当前连接。MySQL 写路径使用行锁。
        if for_update and isinstance(self, MySqlTradeRunRepository):
            sql += " FOR UPDATE"
        return self._one(self.conn.execute(sql, (run_id,)))

    def list_runs(self, include_deleted=False):
        sql = "SELECT * FROM trade_run"
        if not include_deleted:
            sql += " WHERE deleted_at IS NULL"
        return self._many(self.conn.execute(sql + " ORDER BY run_id DESC"))

    def list_running_runs(self):
        return self._many(self.conn.execute(
            "SELECT * FROM trade_run WHERE status='running' AND deleted_at IS NULL ORDER BY run_id"
        ))

    def update_run(self, run_id, **values):
        values["run_id"] = run_id
        sets = ", ".join(f"{key}=?" for key in values if key != "run_id")
        params = [value for key, value in values.items() if key != "run_id"] + [run_id]
        self.conn.execute(f"UPDATE trade_run SET {sets} WHERE run_id=?", params)

    def add_audit(self, run_id, event_type, message, payload=None):
        self.conn.execute("INSERT INTO audit_event(run_id,event_type,message,payload_json,created_at) VALUES (?,?,?,?,?)", (run_id, event_type, message, json.dumps(payload or {}, ensure_ascii=False), self.now()))

    def add_risk_event(self, run_id, event_code, severity, message, payload=None):
        self.conn.execute("INSERT INTO risk_event(run_id,event_code,severity,message,payload_json,created_at) VALUES (?,?,?,?,?,?)", (run_id, event_code, severity, message, json.dumps(payload or {}, ensure_ascii=False), self.now()))

    def list_events(self, run_id, limit=50):
        return self._many(self.conn.execute("SELECT * FROM audit_event WHERE run_id=? ORDER BY event_id DESC LIMIT ?", (run_id, limit)))

    def insert_plan(self, values):
        cur = self.conn.execute("INSERT INTO signal_plan(run_id,ts_code,asset_type,side,suggested_qty,reference_price,min_price,max_price,status,data_status,blocked_reason,valid_from,expires_at,filled_qty,reason,evidence_json,signal_source,plan_window,as_of,observation_id,execution_confirmation_required,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
        return cur.lastrowid

    def get_plan(self, plan_id):
        return self._one(self.conn.execute("SELECT * FROM signal_plan WHERE plan_id=?", (plan_id,)))

    def list_plans(self, run_id):
        return self._many(self.conn.execute("SELECT * FROM signal_plan WHERE run_id=? ORDER BY plan_id DESC", (run_id,)))

    def list_plans_for_window(self, run_id, signal_source, plan_window, plan_date=None):
        sql = "SELECT * FROM signal_plan WHERE run_id=? AND signal_source=? AND plan_window=?"
        params = [run_id, signal_source, plan_window]
        if plan_date:
            sql += " AND DATE(as_of)=?"
            params.append(plan_date)
        return self._many(self.conn.execute(sql + " ORDER BY plan_id", params))

    def update_plan(self, plan_id, **values):
        sets = ", ".join(f"{key}=?" for key in values)
        self.conn.execute(f"UPDATE signal_plan SET {sets} WHERE plan_id=?", list(values.values()) + [plan_id])

    def get_fill_by_key(self, key):
        return self._one(self.conn.execute("SELECT * FROM execution_fill WHERE idempotency_key=?", (key,)))

    def insert_fill(self, values):
        cur = self.conn.execute("INSERT INTO execution_fill(run_id,plan_id,idempotency_key,ts_code,side,qty,price,fee,executed_at,source,note,broker_quote_confirmed,quote_checked_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
        return cur.lastrowid

    def list_fills(self, run_id):
        return self._many(self.conn.execute("SELECT * FROM execution_fill WHERE run_id=? ORDER BY fill_id DESC", (run_id,)))

    def plan_fill_qty(self, plan_id):
        row = self._one(self.conn.execute("SELECT COALESCE(SUM(qty),0) AS filled_qty FROM execution_fill WHERE plan_id=?", (plan_id,)))
        return int(row["filled_qty"] if row else 0)

    def get_position(self, run_id, ts_code):
        return self._one(self.conn.execute("SELECT * FROM run_position WHERE run_id=? AND ts_code=?", (run_id, ts_code)))

    def list_positions(self, run_id):
        return self._many(self.conn.execute("SELECT * FROM run_position WHERE run_id=? AND qty>0 ORDER BY ts_code", (run_id,)))

    def upsert_position(self, values):
        self.conn.execute("INSERT INTO run_position(run_id,ts_code,asset_type,qty,sellable_qty,avg_cost,realized_pnl,open_date,updated_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,ts_code) DO UPDATE SET qty=excluded.qty,sellable_qty=excluded.sellable_qty,avg_cost=excluded.avg_cost,realized_pnl=excluded.realized_pnl,open_date=excluded.open_date,updated_at=excluded.updated_at", values)

    def rollover_sellable(self, run_id, trade_date):
        self.conn.execute("UPDATE run_position SET sellable_qty=qty, updated_at=? WHERE run_id=? AND open_date<?", (self.now(), run_id, trade_date))

    def add_cash_ledger(self, run_id, fill_id, entry_type, amount, balance_after):
        self.conn.execute("INSERT INTO run_cash_ledger(run_id,fill_id,entry_type,amount,balance_after,created_at) VALUES (?,?,?,?,?,?)", (run_id, fill_id, entry_type, amount, balance_after, self.now()))

    def plan_counts(self, run_id):
        rows = self._many(self.conn.execute("SELECT status,COUNT(*) AS count FROM signal_plan WHERE run_id=? GROUP BY status", (run_id,)))
        return {r["status"]: r["count"] for r in rows}

    def insert_observation(self, values):
        cur = self.conn.execute(
            "INSERT INTO market_data_observation(run_id,ts_code,source,market_time,received_at,delay_seconds,completeness,snapshot_ref,payload_json) VALUES (?,?,?,?,?,?,?,?,?)",
            values,
        )
        return cur.lastrowid

    def insert_comparison(self, values):
        self.conn.execute(
            "INSERT INTO plan_comparison(run_id,plan_date,plan_window,ts_code,side,comparison_type,primary_plan_id,shadow_plan_id,mirrored_fill_id,opportunity_note,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id,plan_date,plan_window,ts_code,side) DO UPDATE SET comparison_type=excluded.comparison_type,primary_plan_id=excluded.primary_plan_id,shadow_plan_id=excluded.shadow_plan_id,opportunity_note=excluded.opportunity_note",
            values,
        )

    def mark_comparison_fill(self, primary_plan_id, fill_id):
        self.conn.execute("UPDATE plan_comparison SET mirrored_fill_id=? WHERE primary_plan_id=? AND comparison_type='overlap'", (fill_id, primary_plan_id))

    def claim_plan_generation(self, run_id, plan_window, plan_date):
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO plan_generation(run_id,plan_window,plan_date,status,started_at) VALUES (?,?,?,?,?)",
            (run_id, plan_window, plan_date, "processing", self.now()),
        )
        return bool(cur.rowcount)

    def finish_plan_generation(self, run_id, plan_window, plan_date, status, error_message=None):
        self.conn.execute("UPDATE plan_generation SET status=?, completed_at=?, error_message=? WHERE run_id=? AND plan_window=? AND plan_date=?", (status, self.now(), error_message, run_id, plan_window, plan_date))

    def get_plan_generation(self, run_id, plan_window, plan_date):
        return self._one(self.conn.execute(
            "SELECT * FROM plan_generation WHERE run_id=? AND plan_window=? AND plan_date=?",
            (run_id, plan_window, plan_date),
        ))

    def list_comparisons(self, run_id):
        return self._many(self.conn.execute("SELECT * FROM plan_comparison WHERE run_id=? ORDER BY comparison_id DESC", (run_id,)))

    def list_all_positions(self, run_id):
        return self._many(self.conn.execute("SELECT * FROM run_position WHERE run_id=? ORDER BY ts_code", (run_id,)))


class _MySqlConnectionAdapter:
    """为每个工作线程维护独立的 PyMySQL 连接。

    FastAPI 会把同步路由分派给线程池。PyMySQL 连接不是线程安全的，不能把
    单条连接作为进程级单例共享，否则并发查询会交叉读写 MySQL 协议包。
    """

    def __init__(self, connection=None, connection_factory=None):
        if connection is None and connection_factory is None:
            raise ValueError("必须提供 MySQL 连接或连接工厂")
        self._connection_factory = connection_factory
        self._bootstrap_connection = connection
        self._bootstrap_lock = threading.Lock()
        self._bootstrap_claimed = False
        self._local = threading.local()

    def _connection(self):
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            return connection

        if self._connection_factory is not None:
            connection = self._connection_factory()
        else:
            # 仅保留给显式注入连接的兼容路径；生产配置必须传入工厂，才能让
            # 每个 FastAPI 工作线程获得独立连接。
            with self._bootstrap_lock:
                if self._bootstrap_claimed:
                    raise RuntimeError("共享 MySQL 连接不能跨线程使用；请提供 connection_factory")
                connection = self._bootstrap_connection
                self._bootstrap_claimed = True

        self._local.connection = connection
        self._local.in_transaction = False
        return connection

    def _ping_if_safe(self, connection):
        # 事务中若连接中断，应让调用方整体回滚，不能悄悄重连到新事务。
        if not getattr(self._local, "in_transaction", False):
            connection.ping(reconnect=True)

    def execute(self, sql, params=()):
        connection = self._connection()
        self._ping_if_safe(connection)
        cur = connection.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        return cur

    def commit(self):
        try:
            self._connection().commit()
        finally:
            self._local.in_transaction = False

    def rollback(self):
        try:
            self._connection().rollback()
        finally:
            self._local.in_transaction = False

    def begin(self):
        connection = self._connection()
        self._ping_if_safe(connection)
        connection.begin()
        self._local.in_transaction = True

    def close_current_thread(self):
        """关闭当前线程持有的连接，供进程退出或受控工作线程清理使用。"""
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            try:
                connection.close()
            finally:
                self._local.connection = None
                self._local.in_transaction = False


class MySqlTradeRunRepository(SqliteTradeRunRepository):
    """MySQL/MariaDB 实现。

    由应用启动器调用 `from_config()` 创建；表必须先执行
    `sql/trade_run_schema.sql`。测试仍应使用 SQLite 仓储，避免触碰业务库。
    """

    def __init__(self, connection=None, connection_factory=None):
        self.conn = _MySqlConnectionAdapter(connection, connection_factory)

    @classmethod
    def from_config(cls):
        import pymysql
        from config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
        connection_options = {
            "host": DB_HOST,
            "port": DB_PORT,
            "user": DB_USER,
            "password": DB_PASSWORD,
            "database": DB_NAME,
            "charset": "utf8mb4",
            "autocommit": False,
            "cursorclass": pymysql.cursors.DictCursor,
            "connect_timeout": 10,
        }
        return cls(connection_factory=lambda: pymysql.connect(**connection_options))

    def initialize(self):
        """不建表；只在确认已迁移后确保策略种子存在。"""
        now = self.now()
        with self.transaction():
            for code, name, description, fingerprint in [
                ("short_term", "短线", "1-3 个交易日的盘中条件计划", "short-v1"),
                ("medium_term", "中线", "1-4 周趋势与行业强度计划", "medium-v1"),
                ("long_term", "长线", "1-3 个月低换手趋势计划", "long-v1"),
            ]:
                self.conn.execute("INSERT IGNORE INTO strategy_definition(code,name,description) VALUES (?,?,?)", (code, name, description))
                self.conn.execute("INSERT IGNORE INTO strategy_version(strategy_code,version_no,algorithm_fingerprint,config_json,signal_source,created_at) VALUES (?,?,?,JSON_OBJECT(),?,?)", (code, 1, fingerprint, "legacy", now))
                self.conn.execute("INSERT IGNORE INTO strategy_version(strategy_code,version_no,algorithm_fingerprint,config_json,signal_source,created_at) VALUES (?,?,?,JSON_OBJECT(),?,?)", (code, 2, f"rule-{code}-v1", "new", now))

    @contextlib.contextmanager
    def transaction(self):
        try:
            self.conn.begin()
            yield self
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def upsert_position(self, values):
        self.conn.execute(
            "INSERT INTO run_position(run_id,ts_code,asset_type,qty,sellable_qty,avg_cost,realized_pnl,open_date,updated_at) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON DUPLICATE KEY UPDATE qty=VALUES(qty),sellable_qty=VALUES(sellable_qty),avg_cost=VALUES(avg_cost),realized_pnl=VALUES(realized_pnl),open_date=VALUES(open_date),updated_at=VALUES(updated_at)",
            values,
        )

    def insert_comparison(self, values):
        self.conn.execute(
            "INSERT INTO plan_comparison(run_id,plan_date,plan_window,ts_code,side,comparison_type,primary_plan_id,shadow_plan_id,mirrored_fill_id,opportunity_note,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON DUPLICATE KEY UPDATE comparison_type=VALUES(comparison_type),primary_plan_id=VALUES(primary_plan_id),shadow_plan_id=VALUES(shadow_plan_id),opportunity_note=VALUES(opportunity_note)",
            values,
        )

    def claim_plan_generation(self, run_id, plan_window, plan_date):
        cur = self.conn.execute(
            "INSERT IGNORE INTO plan_generation(run_id,plan_window,plan_date,status,started_at) VALUES (?,?,?,?,?)",
            (run_id, plan_window, plan_date, "processing", self.now()),
        )
        return bool(cur.rowcount)
