"""交易实例持久化层。

`SqliteTradeRunRepository` 用于隔离测试和本地演示；生产 MySQL 使用同一套
字段定义（见 sql/trade_run_schema.sql），通过 DB-API 连接初始化本仓储即可。
所有会改变账务事实的方法均要求调用方处于 `transaction()` 中。
"""
import contextlib
import json
import sqlite3
from datetime import datetime


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_definition (
  code TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_version (
  version_id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_code TEXT NOT NULL,
  version_no INTEGER NOT NULL, algorithm_fingerprint TEXT NOT NULL,
  config_json TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(strategy_code, version_no)
);
CREATE TABLE IF NOT EXISTS trade_run (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
  strategy_code TEXT NOT NULL, strategy_version_id INTEGER NOT NULL,
  status TEXT NOT NULL, active_strategy_code TEXT UNIQUE,
  initial_capital REAL NOT NULL, current_cash REAL NOT NULL,
  max_position_pct REAL NOT NULL, asset_types_json TEXT NOT NULL,
  frozen_config_json TEXT NOT NULL, created_at TEXT NOT NULL,
  started_at TEXT, paused_at TEXT, ended_at TEXT, deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS signal_plan (
  plan_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL,
  ts_code TEXT NOT NULL, asset_type TEXT NOT NULL, side TEXT NOT NULL,
  suggested_qty INTEGER NOT NULL, reference_price REAL NOT NULL,
  min_price REAL, max_price REAL, status TEXT NOT NULL, data_status TEXT NOT NULL,
  blocked_reason TEXT, valid_from TEXT, expires_at TEXT, filled_qty INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL,
  evidence_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_fill (
  fill_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL,
  plan_id INTEGER, idempotency_key TEXT NOT NULL UNIQUE, ts_code TEXT NOT NULL,
  side TEXT NOT NULL, qty INTEGER NOT NULL, price REAL NOT NULL, fee REAL NOT NULL,
  executed_at TEXT NOT NULL, source TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL
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
                "INSERT OR IGNORE INTO strategy_version(strategy_code,version_no,algorithm_fingerprint,config_json,created_at) VALUES (?,?,?,?,?)",
                (code, 1, fingerprint, "{}", now),
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

    def latest_strategy_version(self, code):
        return self._one(self.conn.execute("SELECT * FROM strategy_version WHERE strategy_code=? ORDER BY version_no DESC LIMIT 1", (code,)))

    def list_strategies(self):
        return self._many(self.conn.execute("SELECT d.code,d.name,d.description,v.version_id,v.version_no,v.algorithm_fingerprint FROM strategy_definition d JOIN strategy_version v ON v.strategy_code=d.code WHERE v.version_no=(SELECT MAX(version_no) FROM strategy_version WHERE strategy_code=d.code) ORDER BY d.code"))

    def list_versions(self, code):
        return self._many(self.conn.execute("SELECT * FROM strategy_version WHERE strategy_code=? ORDER BY version_no DESC", (code,)))

    def insert_run(self, values):
        cur = self.conn.execute("INSERT INTO trade_run(name,strategy_code,strategy_version_id,status,initial_capital,current_cash,max_position_pct,asset_types_json,frozen_config_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", values)
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

    def update_run(self, run_id, **values):
        values["run_id"] = run_id
        sets = ", ".join(f"{key}=?" for key in values if key != "run_id")
        params = [value for key, value in values.items() if key != "run_id"] + [run_id]
        self.conn.execute(f"UPDATE trade_run SET {sets} WHERE run_id=?", params)

    def add_audit(self, run_id, event_type, message, payload=None):
        self.conn.execute("INSERT INTO audit_event(run_id,event_type,message,payload_json,created_at) VALUES (?,?,?,?,?)", (run_id, event_type, message, json.dumps(payload or {}, ensure_ascii=False), self.now()))

    def list_events(self, run_id, limit=50):
        return self._many(self.conn.execute("SELECT * FROM audit_event WHERE run_id=? ORDER BY event_id DESC LIMIT ?", (run_id, limit)))

    def insert_plan(self, values):
        cur = self.conn.execute("INSERT INTO signal_plan(run_id,ts_code,asset_type,side,suggested_qty,reference_price,min_price,max_price,status,data_status,blocked_reason,valid_from,expires_at,filled_qty,reason,evidence_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
        return cur.lastrowid

    def get_plan(self, plan_id):
        return self._one(self.conn.execute("SELECT * FROM signal_plan WHERE plan_id=?", (plan_id,)))

    def list_plans(self, run_id):
        return self._many(self.conn.execute("SELECT * FROM signal_plan WHERE run_id=? ORDER BY plan_id DESC", (run_id,)))

    def update_plan(self, plan_id, **values):
        sets = ", ".join(f"{key}=?" for key in values)
        self.conn.execute(f"UPDATE signal_plan SET {sets} WHERE plan_id=?", list(values.values()) + [plan_id])

    def get_fill_by_key(self, key):
        return self._one(self.conn.execute("SELECT * FROM execution_fill WHERE idempotency_key=?", (key,)))

    def insert_fill(self, values):
        cur = self.conn.execute("INSERT INTO execution_fill(run_id,plan_id,idempotency_key,ts_code,side,qty,price,fee,executed_at,source,note,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", values)
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

    def list_all_positions(self, run_id):
        return self._many(self.conn.execute("SELECT * FROM run_position WHERE run_id=? ORDER BY ts_code", (run_id,)))


class _MySqlConnectionAdapter:
    """把 PyMySQL 连接适配为本仓储所需的最小 DB-API 表面。"""

    def __init__(self, conn):
        self.raw = conn

    def execute(self, sql, params=()):
        cur = self.raw.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        return cur

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def begin(self):
        self.raw.begin()


class MySqlTradeRunRepository(SqliteTradeRunRepository):
    """MySQL/MariaDB 实现。

    由应用启动器调用 `from_config()` 创建；表必须先执行
    `sql/trade_run_schema.sql`。测试仍应使用 SQLite 仓储，避免触碰业务库。
    """

    def __init__(self, connection):
        self.conn = _MySqlConnectionAdapter(connection)

    @classmethod
    def from_config(cls):
        import pymysql
        from config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
        conn = pymysql.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
            database=DB_NAME, charset="utf8mb4", autocommit=False,
            cursorclass=pymysql.cursors.DictCursor, connect_timeout=10,
        )
        return cls(conn)

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
                self.conn.execute("INSERT IGNORE INTO strategy_version(strategy_code,version_no,algorithm_fingerprint,config_json,created_at) VALUES (?,?,?,JSON_OBJECT(),?)", (code, 1, fingerprint, now))

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
