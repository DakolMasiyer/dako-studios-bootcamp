#!/usr/bin/env python3
"""
Adds onboarding columns to students table and creates creative_tech_applications.
Safe to run multiple times (idempotent).
"""
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "bootcamp.db"

NEW_STUDENT_COLS = [
    "ALTER TABLE students ADD COLUMN skill_level TEXT DEFAULT NULL",
    "ALTER TABLE students ADD COLUMN country TEXT DEFAULT NULL",
    "ALTER TABLE students ADD COLUMN preferred_lang TEXT NOT NULL DEFAULT 'en'",
]

CREATE_CT_APPLICATIONS = """
CREATE TABLE IF NOT EXISTS creative_tech_applications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    email      TEXT NOT NULL,
    background TEXT NOT NULL,
    motivation TEXT NOT NULL,
    country    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _migrate_sqlite():
    if not DB_PATH.exists():
        print(f"SQLite DB not found at {DB_PATH} — skipping SQLite migration")
        return
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    for sql in NEW_STUDENT_COLS:
        try:
            conn.execute(sql)
            print(f"  SQLite: {sql.split('ADD COLUMN')[1].strip().split()[0]} added")
        except sqlite3.OperationalError:
            print(f"  SQLite: {sql.split('ADD COLUMN')[1].strip().split()[0]} already exists")
    conn.execute(CREATE_CT_APPLICATIONS)
    print("  SQLite: creative_tech_applications table ready")
    conn.commit()
    conn.close()


def _migrate_postgres(database_url: str):
    try:
        import psycopg
    except ImportError:
        print("psycopg not installed — skipping Postgres migration")
        return
    conn = psycopg.connect(database_url, autocommit=True)
    for sql in NEW_STUDENT_COLS:
        col = sql.split("ADD COLUMN")[1].strip().split()[0]
        try:
            conn.execute(sql.replace("?", "%s"))
            print(f"  Postgres: {col} added")
        except Exception as e:
            if "already exists" in str(e).lower() or "duplicate column" in str(e).lower():
                print(f"  Postgres: {col} already exists")
            else:
                print(f"  Postgres: {col} — {e}")
    try:
        conn.execute(
            CREATE_CT_APPLICATIONS.replace("INTEGER PRIMARY KEY AUTOINCREMENT",
                                           "SERIAL PRIMARY KEY")
                                  .replace("datetime('now')", "NOW()")
        )
        print("  Postgres: creative_tech_applications table ready")
    except Exception as e:
        if "already exists" in str(e).lower():
            print("  Postgres: creative_tech_applications already exists")
        else:
            print(f"  Postgres: {e}")
    conn.close()


def migrate():
    print("Running onboarding migration...")
    _migrate_sqlite()
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        from pathlib import Path as P
        env_file = P(__file__).parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("DATABASE_URL="):
                    db_url = line.partition("=")[2].strip()
                    break
    if db_url:
        print("Running Postgres migration...")
        _migrate_postgres(db_url)
    print("Done.")


if __name__ == "__main__":
    migrate()
