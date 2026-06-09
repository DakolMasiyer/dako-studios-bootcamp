#!/usr/bin/env python3
import sqlite3
from pathlib import Path

DB_PATH = Path("data/bootcamp.db")

def migrate():
    print("Starting Phase 3C (Assessment Attempt Governance) migration...")
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    cur = conn.cursor()

    cur.executescript("""
    PRAGMA foreign_keys=OFF;
    BEGIN TRANSACTION;

    CREATE TABLE IF NOT EXISTS assessment_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assessment_id INTEGER NOT NULL REFERENCES assessments(id),
        student_id INTEGER NOT NULL REFERENCES students(id),
        submission_id INTEGER REFERENCES submissions(id),
        attempt_number INTEGER NOT NULL,
        session_status TEXT NOT NULL DEFAULT 'active',
        started_at TEXT NOT NULL DEFAULT (datetime('now')),
        expires_at TEXT,
        submitted_at TEXT,
        autosave_at TEXT,
        remaining_seconds INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    COMMIT;
    PRAGMA foreign_keys=ON;
    """)

    conn.close()
    print("Phase 3C migration complete.")

if __name__ == "__main__":
    migrate()
