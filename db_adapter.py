from __future__ import annotations

import datetime
import os
import queue
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Optional


def _normalize_row(row):
    """Convert datetime/date objects in a Postgres dict row to ISO strings."""
    if row is None or not isinstance(row, dict):
        return row
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime.datetime):
            out[k] = v.isoformat(sep=" ", timespec="seconds")
        elif isinstance(v, datetime.date):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


AUTO_ID_TABLES = {
    "ai_feedback",
    "assessment_attempts",
    "assessment_results",
    "assessments",
    "coaches",
    "cohorts",
    "exam_attempt_questions",
    "exam_choices",
    "exam_questions",
    "exams",
    "grading_breakdowns",
    "grading_results",
    "overrides",
    "payments",
    "rubric_rules",
    "rubric_sections",
    "rubrics",
    "students",
    "submission_answers",
    "submission_files",
    "submissions",
    "webhook_logs",
}

INSERT_RE = re.compile(
    r"^\s*INSERT\s+(?:OR\s+(?P<conflict>IGNORE|REPLACE)\s+)?INTO\s+"
    r"(?P<table>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?:\((?P<columns>[^)]*)\))?\s*VALUES\s*\(",
    re.IGNORECASE | re.DOTALL,
)


class CursorProxy:
    def __init__(self, cursor: Any, lastrowid: Any = None, buffered_row: Any = None, normalize=False):
        self._cursor = cursor
        self._lastrowid = lastrowid
        self._buffered_row = buffered_row
        self._normalize = normalize

    def _norm(self, row):
        return _normalize_row(row) if self._normalize else row

    @property
    def rowcount(self) -> int:
        return getattr(self._cursor, "rowcount", -1)

    @property
    def lastrowid(self) -> Any:
        if self._lastrowid is not None:
            return self._lastrowid
        return getattr(self._cursor, "lastrowid", None)

    @property
    def description(self):
        return getattr(self._cursor, "description", None)

    def fetchone(self):
        if self._buffered_row is not None:
            row = self._buffered_row
            self._buffered_row = None
            return self._norm(row)
        return self._norm(self._cursor.fetchone())

    def fetchall(self):
        rows = []
        first = self.fetchone()
        if first is not None:
            rows.append(first)
        rows.extend(self._norm(r) for r in self._cursor.fetchall())
        return rows

    def __iter__(self):
        if self._buffered_row is not None:
            yield self.fetchone()
        for row in self._cursor:
            yield self._norm(row)


class DatabaseAdapter:
    backend_name = "unknown"

    def get_connection(self):
        raise NotImplementedError

    def return_connection(self, conn):
        raise NotImplementedError

    def query(self, sql, params=()):
        conn = self.get_connection()
        try:
            cur = conn.execute(sql, params)
            rows = cur.fetchall()
            conn.commit()
            return rows
        except Exception:
            conn.rollback()
            raise
        finally:
            self.return_connection(conn)

    def one(self, sql, params=()):
        conn = self.get_connection()
        try:
            cur = conn.execute(sql, params)
            row = cur.fetchone()
            conn.commit()
            return row
        except Exception:
            conn.rollback()
            raise
        finally:
            self.return_connection(conn)

    def run(self, sql, params=()):
        conn = self.get_connection()
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid
        except Exception:
            conn.rollback()
            raise
        finally:
            self.return_connection(conn)

    @contextmanager
    def transaction(self, immediate=False):
        conn = self.get_connection()
        try:
            conn._begin(immediate=immediate)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self.return_connection(conn)


class _BaseConnectionProxy:
    def __init__(self, adapter: DatabaseAdapter, raw_conn: Any):
        self._adapter = adapter
        self._raw_conn = raw_conn

    def _translate_sql(self, sql: str) -> tuple[str, bool]:
        return sql, False

    def _begin(self, immediate=False):
        raise NotImplementedError

    def execute(self, sql, params=()):
        sql, needs_lastrowid = self._translate_sql(sql)
        cur = self._raw_conn.execute(sql, params)
        buffered_row = None
        lastrowid = None
        if needs_lastrowid and getattr(cur, "description", None):
            buffered_row = cur.fetchone()
            if buffered_row is not None:
                if isinstance(buffered_row, dict):
                    lastrowid = buffered_row.get("id")
                else:
                    try:
                        lastrowid = buffered_row["id"]
                    except Exception:
                        lastrowid = None
        return CursorProxy(cur, lastrowid=lastrowid, buffered_row=buffered_row)

    def commit(self):
        self._raw_conn.commit()

    def rollback(self):
        self._raw_conn.rollback()

    @property
    def raw(self):
        return self._raw_conn

    def __getattr__(self, name):
        return getattr(self._raw_conn, name)


class SQLiteConnectionProxy(_BaseConnectionProxy):
    def _begin(self, immediate=False):
        self._raw_conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")


class PostgresConnectionProxy(_BaseConnectionProxy):
    def _translate_sql(self, sql: str) -> tuple[str, bool]:
        sql = sql.strip()
        upper = sql.upper()

        if upper == "BEGIN IMMEDIATE":
            return "BEGIN", False

        needs_lastrowid = False

        if upper.startswith("INSERT OR REPLACE INTO CURRICULUM"):
            return self._translate_curriculum_replace(sql), False

        if upper.startswith("INSERT OR IGNORE INTO "):
            sql = re.sub(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+", "INSERT INTO ", sql, count=1, flags=re.IGNORECASE)
            if " ON CONFLICT " not in upper:
                sql += " ON CONFLICT DO NOTHING"

        sql = sql.replace("?", "%s")

        match = INSERT_RE.match(sql)
        if match and "RETURNING" not in upper:
            table = match.group("table").lower()
            columns = match.group("columns")
            if table in AUTO_ID_TABLES:
                inserted_columns = set()
                if columns:
                    inserted_columns = {part.strip().strip('"').strip("`").lower() for part in columns.split(",")}
                if "id" not in inserted_columns:
                    sql += " RETURNING id"
                    needs_lastrowid = True

        return sql, needs_lastrowid

    def _translate_curriculum_replace(self, sql: str) -> str:
        match = re.match(
            r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\s+curriculum\s*\((?P<columns>[^)]*)\)\s*VALUES\s*\((?P<values>.*)\)\s*$",
            sql,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return sql

        columns = [part.strip().strip('"').strip("`") for part in match.group("columns").split(",")]
        values = match.group("values").strip()
        update_columns = [col for col in columns if col != "day"]
        if not update_columns:
            return f"INSERT INTO curriculum ({', '.join(columns)}) VALUES ({values}) ON CONFLICT (day) DO NOTHING"

        update_clause = ", ".join(f"{col}=EXCLUDED.{col}" for col in update_columns)
        return f"INSERT INTO curriculum ({', '.join(columns)}) VALUES ({values}) ON CONFLICT (day) DO UPDATE SET {update_clause}"

    def execute(self, sql, params=()):
        sql, needs_lastrowid = self._translate_sql(sql)
        cur = self._raw_conn.execute(sql, params)
        buffered_row = None
        lastrowid = None
        if needs_lastrowid and getattr(cur, "description", None):
            buffered_row = cur.fetchone()
            if buffered_row is not None:
                lastrowid = buffered_row.get("id") if isinstance(buffered_row, dict) else None
        return CursorProxy(cur, lastrowid=lastrowid, buffered_row=buffered_row, normalize=True)

    def _begin(self, immediate=False):
        self._raw_conn.execute("BEGIN")


class SQLiteAdapter(DatabaseAdapter):
    backend_name = "sqlite"

    def __init__(self, db_path, max_connections=20, timeout=30):
        self.db_path = Path(db_path)
        self.max_connections = max_connections
        self.timeout = timeout
        self.pool = queue.Queue(maxsize=max_connections)
        for _ in range(max_connections):
            self.pool.put(self._create_connection())

    def _create_connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=self.timeout, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def get_connection(self):
        try:
            raw = self.pool.get(timeout=10)
        except queue.Empty as exc:
            raise sqlite3.OperationalError("Database connection pool exhausted") from exc
        return SQLiteConnectionProxy(self, raw)

    def return_connection(self, conn):
        raw = conn.raw if hasattr(conn, "raw") else conn
        self.pool.put(raw)


class PostgresAdapter(DatabaseAdapter):
    backend_name = "postgresql"

    def __init__(self, database_url, max_connections=20):
        self.database_url = database_url
        self.max_connections = max_connections
        self.pool = queue.Queue(maxsize=max_connections)
        for _ in range(max_connections):
            self.pool.put(self._create_connection())

    def _create_connection(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except Exception as exc:
            raise RuntimeError(
                "PostgreSQL support requires psycopg. Install psycopg[binary] before using DATABASE_URL."
            ) from exc

        conn = psycopg.connect(self.database_url, row_factory=dict_row)
        conn.autocommit = False
        return conn

    def _is_connection_alive(self, conn) -> bool:
        try:
            conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    def get_connection(self):
        try:
            raw = self.pool.get(timeout=10)
        except queue.Empty as exc:
            raise RuntimeError("Database connection pool exhausted") from exc
        if not self._is_connection_alive(raw):
            try:
                raw.close()
            except Exception:
                pass
            raw = self._create_connection()
        return PostgresConnectionProxy(self, raw)

    def return_connection(self, conn):
        raw = conn.raw if hasattr(conn, "raw") else conn
        self.pool.put(raw)


def get_adapter():
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        print("Database backend: PostgreSQL")
        return PostgresAdapter(database_url)

    print("Database backend: SQLite")
    sqlite_path = Path(os.getenv("SQLITE_PATH", "data/bootcamp.db"))
    if (os.getenv("VERCEL") == "1" or bool(os.getenv("VERCEL_ENV"))) and not sqlite_path.is_absolute():
        sqlite_path = Path("/tmp/bootcamp.db")
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteAdapter(sqlite_path)


db = get_adapter()
DB_POOL = db
query = db.query
one = db.one
run = db.run
