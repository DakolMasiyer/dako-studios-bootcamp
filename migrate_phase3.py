#!/usr/bin/env python3
import sqlite3
from pathlib import Path

DB_PATH = Path("data/bootcamp.db")

def migrate():
    print("Starting Phase 3 database migration...")
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    cur = conn.cursor()

    cur.executescript("""
    PRAGMA foreign_keys=OFF;
    BEGIN TRANSACTION;

    CREATE TABLE IF NOT EXISTS rubrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day INTEGER NOT NULL REFERENCES curriculum(day),
        version INTEGER NOT NULL DEFAULT 1,
        pass_threshold REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS rubric_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rubric_id INTEGER NOT NULL REFERENCES rubrics(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        score_weight REAL NOT NULL DEFAULT 1.0
    );

    CREATE TABLE IF NOT EXISTS rubric_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id INTEGER NOT NULL REFERENCES rubric_categories(id) ON DELETE CASCADE,
        description TEXT NOT NULL,
        max_points REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS grading_feedback_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rubric_id INTEGER NOT NULL REFERENCES rubrics(id) ON DELETE CASCADE,
        condition TEXT NOT NULL, -- 'pass' or 'fail'
        template_text TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS assessment_evaluations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
        rubric_id INTEGER NOT NULL REFERENCES rubrics(id),
        total_score REAL NOT NULL,
        passed INTEGER NOT NULL,
        feedback TEXT,
        overridden INTEGER NOT NULL DEFAULT 0,
        original_score REAL,
        override_reason TEXT,
        graded_by TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS assessment_rule_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        evaluation_id INTEGER NOT NULL REFERENCES assessment_evaluations(id) ON DELETE CASCADE,
        rule_id INTEGER NOT NULL REFERENCES rubric_rules(id),
        points_awarded REAL NOT NULL,
        explanation TEXT
    );

    COMMIT;
    PRAGMA foreign_keys=ON;
    """)

    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
