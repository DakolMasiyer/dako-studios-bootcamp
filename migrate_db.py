#!/usr/bin/env python3
"""
One-time DB migration — adds lesson content, payment, and cohort support.
Safe to run multiple times (all operations are idempotent).
"""
import sqlite3
from db_adapter import db
from pathlib import Path

DB_PATH = Path("data/bootcamp.db")


def column_exists(cur, table, column):
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def table_exists(cur, table):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def migrate():
    if db.backend_name == "postgresql":
        from db_migration import bootstrap_from_bootstrap_file
        import dako_bootcamp_init_db as bootstrap

        bootstrap_from_bootstrap_file(Path("dako_bootcamp_init_db.py"), db, bootstrap.CURRICULUM, bootstrap._hash)
        print("Database already up to date — PostgreSQL bootstrap ensured.")
        return

    if not DB_PATH.exists():
        print("No existing database found — run dako_bootcamp_init_db.py for a fresh install.")
        return

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")

    changes = 0

    # ── curriculum: lesson content columns ────────────────────────────────────
    if table_exists(cur, "curriculum"):
        for col, defn in [
            ("lesson_html",   "TEXT NOT NULL DEFAULT ''"),
            ("video_url",     "TEXT NOT NULL DEFAULT ''"),
            ("lesson_status", "TEXT NOT NULL DEFAULT 'draft'"),
        ]:
            if not column_exists(cur, "curriculum", col):
                cur.execute(f"ALTER TABLE curriculum ADD COLUMN {col} {defn}")
                print(f"  + curriculum.{col}")
                changes += 1

    # ── students: payment + cohort columns ────────────────────────────────────
    if table_exists(cur, "students"):
        for col, defn in [
            ("paid_access", "INTEGER NOT NULL DEFAULT 0"),
            ("cohort_id",   "INTEGER REFERENCES cohorts(id)"),
        ]:
            if not column_exists(cur, "students", col):
                cur.execute(f"ALTER TABLE students ADD COLUMN {col} {defn}")
                print(f"  + students.{col}")
                changes += 1

    # ── cohorts table ─────────────────────────────────────────────────────────
    if not table_exists(cur, "cohorts"):
        cur.execute("""
        CREATE TABLE cohorts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL,
            start_date TEXT    NOT NULL,
            price_usd  REAL    NOT NULL DEFAULT 49.0,
            currency   TEXT    NOT NULL DEFAULT 'USD',
            max_seats  INTEGER,
            is_open    INTEGER NOT NULL DEFAULT 1,
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        )""")
        print("  + table: cohorts")
        changes += 1

    # ── payments table ────────────────────────────────────────────────────────
    if not table_exists(cur, "payments"):
        cur.execute("""
        CREATE TABLE payments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id  INTEGER NOT NULL REFERENCES students(id),
            cohort_id   INTEGER REFERENCES cohorts(id),
            amount      REAL    NOT NULL,
            currency    TEXT    NOT NULL,
            tx_ref      TEXT    UNIQUE NOT NULL,
            flw_ref     TEXT    UNIQUE,
            status      TEXT    NOT NULL DEFAULT 'pending',
            verification_status TEXT NOT NULL DEFAULT 'pending',
            webhook_event_id TEXT,
            webhook_received_at TEXT,
            reconciliation_attempts INTEGER NOT NULL DEFAULT 0,
            last_reconciliation_error TEXT,
            raw_provider_payload TEXT,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            verified_at TEXT
        )""")
        print("  + table: payments")
        changes += 1
    else:
        for col, defn in [
            ("flw_ref", "TEXT UNIQUE"),
            ("verification_status", "TEXT NOT NULL DEFAULT 'pending'"),
            ("webhook_event_id", "TEXT"),
            ("webhook_received_at", "TEXT"),
            ("reconciliation_attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("last_reconciliation_error", "TEXT"),
            ("raw_provider_payload", "TEXT"),
            ("updated_at", "TEXT NOT NULL DEFAULT (datetime('now'))"),
        ]:
            if not column_exists(cur, "payments", col):
                cur.execute(f"ALTER TABLE payments ADD COLUMN {col} {defn}")
                print(f"  + payments.{col}")
                changes += 1

    # ── assessment_jobs table ──────────────────────────────────────────────────
    if not table_exists(cur, "assessment_jobs"):
        cur.execute("""
        CREATE TABLE assessment_jobs (
            id           TEXT PRIMARY KEY,
            submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
            status       TEXT NOT NULL DEFAULT 'pending',
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            started_at   TEXT,
            completed_at TEXT
        )""")
        print("  + table: assessment_jobs")
        changes += 1
    else:
        for col, defn in [
            ("worker_id", "TEXT"),
            ("fence_token", "INTEGER DEFAULT 0"),
            ("retry_count", "INTEGER DEFAULT 0"),
            ("last_error", "TEXT"),
            ("run_at", "TEXT"),
            ("last_heartbeat_at", "TEXT"),
        ]:
            if not column_exists(cur, "assessment_jobs", col):
                cur.execute(f"ALTER TABLE assessment_jobs ADD COLUMN {col} {defn}")
                print(f"  + assessment_jobs.{col}")
                changes += 1

    # ── assessment_results table ───────────────────────────────────────────────
    if not table_exists(cur, "assessment_results"):
        cur.execute("""
        CREATE TABLE assessment_results (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id       TEXT NOT NULL REFERENCES assessment_jobs(id) ON DELETE CASCADE,
            verdict      TEXT NOT NULL,
            feedback     TEXT,
            graded_by    TEXT,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        print("  + table: assessment_results")
        changes += 1

    # ── ai_feedback_jobs table ────────────────────────────────────────────────
    if table_exists(cur, "ai_feedback_jobs"):
        for col, defn in [
            ("worker_id", "TEXT"),
            ("fence_token", "INTEGER DEFAULT 0"),
            ("retry_count", "INTEGER DEFAULT 0"),
            ("last_error", "TEXT"),
            ("run_at", "TEXT"),
            ("last_heartbeat_at", "TEXT"),
        ]:
            if not column_exists(cur, "ai_feedback_jobs", col):
                cur.execute(f"ALTER TABLE ai_feedback_jobs ADD COLUMN {col} {defn}")
                print(f"  + ai_feedback_jobs.{col}")
                changes += 1

    conn.commit()
    conn.close()

    if changes:
        print(f"\nMigration complete — {changes} change(s) applied.")
    else:
        print("Database already up to date — no changes needed.")


if __name__ == "__main__":
    print(f"Migrating {DB_PATH} ...")
    migrate()
