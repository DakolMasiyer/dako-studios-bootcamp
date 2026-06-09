#!/usr/bin/env python3
import sqlite3
from pathlib import Path

DB_PATH = Path("data/bootcamp.db")

def migrate():
    print("Starting Phase 3 (Rubric Engine) migration...")
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    cur = conn.cursor()

    cur.executescript("""
    PRAGMA foreign_keys=OFF;
    BEGIN TRANSACTION;

    DROP TABLE IF EXISTS rubrics;
    DROP TABLE IF EXISTS rubric_categories;
    DROP TABLE IF EXISTS rubric_rules;
    DROP TABLE IF EXISTS assessment_rule_scores;
    DROP TABLE IF EXISTS assessment_evaluations;
    DROP TABLE IF EXISTS grading_feedback_templates;

    CREATE TABLE IF NOT EXISTS rubrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        rubric_version INTEGER NOT NULL DEFAULT 1,
        pass_threshold REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS rubric_sections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rubric_id INTEGER NOT NULL REFERENCES rubrics(id) ON DELETE CASCADE,
        section_name TEXT NOT NULL,
        weight_percentage REAL NOT NULL,
        max_score REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS rubric_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        section_id INTEGER NOT NULL REFERENCES rubric_sections(id) ON DELETE CASCADE,
        rule_key TEXT NOT NULL,
        rule_description TEXT NOT NULL,
        scoring_type TEXT NOT NULL,
        points_possible REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS grading_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
        rubric_id INTEGER NOT NULL REFERENCES rubrics(id),
        total_score REAL NOT NULL,
        pass_fail_status TEXT NOT NULL,
        grading_status TEXT NOT NULL DEFAULT 'completed',
        graded_at TEXT NOT NULL DEFAULT (datetime('now')),
        rubric_version INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS grading_breakdowns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grading_result_id INTEGER NOT NULL REFERENCES grading_results(id) ON DELETE CASCADE,
        section_id INTEGER NOT NULL REFERENCES rubric_sections(id),
        awarded_score REAL NOT NULL,
        max_score REAL NOT NULL,
        feedback_text TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS overrides (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grading_result_id INTEGER NOT NULL REFERENCES grading_results(id) ON DELETE CASCADE,
        new_total_score REAL NOT NULL,
        new_pass_fail_status TEXT NOT NULL,
        override_reason TEXT NOT NULL,
        reviewer_attribution TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- Seed a default rubric for Day 1
    INSERT INTO rubrics (id, name, description, pass_threshold) 
    SELECT 1, 'Standard Assignment', 'Baseline rubric', 70.0 
    WHERE NOT EXISTS (SELECT 1 FROM rubrics WHERE id=1);

    INSERT INTO rubric_sections (id, rubric_id, section_name, weight_percentage, max_score)
    SELECT 1, 1, 'Completeness', 100.0, 100.0
    WHERE NOT EXISTS (SELECT 1 FROM rubric_sections WHERE id=1);

    INSERT INTO rubric_rules (id, section_id, rule_key, rule_description, scoring_type, points_possible)
    SELECT 1, 1, 'has_answer', 'Student provided an answer', 'boolean', 100.0
    WHERE NOT EXISTS (SELECT 1 FROM rubric_rules WHERE id=1);

    COMMIT;
    PRAGMA foreign_keys=ON;
    """)

    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
