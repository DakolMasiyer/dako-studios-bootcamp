#!/usr/bin/env python3
import sqlite3
from pathlib import Path

DB_PATH = Path("data/bootcamp.db")

def migrate():
    print("Starting Phase 3B (AI Feedback) migration...")
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    cur = conn.cursor()

    cur.executescript("""
    PRAGMA foreign_keys=OFF;
    BEGIN TRANSACTION;

    CREATE TABLE IF NOT EXISTS ai_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grading_result_id INTEGER NOT NULL REFERENCES grading_results(id) ON DELETE CASCADE,
        generated_feedback TEXT NOT NULL,
        strengths_summary TEXT NOT NULL,
        weaknesses_summary TEXT NOT NULL,
        improvement_suggestions TEXT NOT NULL,
        ai_model_name TEXT NOT NULL,
        generated_at TEXT NOT NULL DEFAULT (datetime('now')),
        feedback_status TEXT NOT NULL DEFAULT 'visible'
    );

    CREATE TABLE IF NOT EXISTS ai_feedback_jobs (
        id TEXT PRIMARY KEY,
        grading_result_id INTEGER NOT NULL REFERENCES grading_results(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        started_at TEXT,
        completed_at TEXT,
        worker_id TEXT,
        fence_token INTEGER DEFAULT 0,
        retry_count INTEGER DEFAULT 0,
        last_error TEXT,
        run_at TEXT,
        last_heartbeat_at TEXT
    );

    COMMIT;
    PRAGMA foreign_keys=ON;
    """)

    conn.close()
    print("Phase 3B migration complete.")

if __name__ == "__main__":
    migrate()
