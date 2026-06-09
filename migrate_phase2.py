#!/usr/bin/env python3
import sqlite3
from pathlib import Path

DB_PATH = Path("data/bootcamp.db")

def migrate():
    print("Starting Phase 2 database migration...")
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    cur = conn.cursor()
    
    # Check if migration already ran
    columns = [row[1] for row in cur.execute("PRAGMA table_info(payments)").fetchall()]
    if "reconciliation_attempts" in columns:
        print("Migration already applied. Skipping.")
        return

    cur.executescript("""
    PRAGMA foreign_keys=OFF;
    BEGIN TRANSACTION;

    CREATE TABLE IF NOT EXISTS payments_v2 (
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
    );

    INSERT INTO payments_v2 (id, student_id, cohort_id, amount, currency, tx_ref, flw_ref, status, created_at, verified_at, verification_status)
    SELECT id, student_id, cohort_id, amount, currency, tx_ref, flw_ref, status, created_at, verified_at, CASE WHEN status='success' THEN 'verified' ELSE 'pending' END
    FROM payments;

    DROP TABLE payments;
    ALTER TABLE payments_v2 RENAME TO payments;

    CREATE TABLE IF NOT EXISTS webhook_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tx_ref TEXT,
        flw_ref TEXT,
        event_type TEXT,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    COMMIT;
    PRAGMA foreign_keys=ON;
    """)
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
