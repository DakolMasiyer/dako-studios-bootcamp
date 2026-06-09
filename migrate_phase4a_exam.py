#!/usr/bin/env python3
import sqlite3
from pathlib import Path

DB_PATH = Path("data/bootcamp.db")

def migrate():
    print("Starting Phase 4A (Examination Engine) migration...")
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    cur = conn.cursor()

    cur.executescript("""
    PRAGMA foreign_keys=OFF;
    BEGIN TRANSACTION;

    CREATE TABLE IF NOT EXISTS exams (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assessment_id INTEGER NOT NULL REFERENCES assessments(id),
        exam_title TEXT NOT NULL,
        duration_minutes INTEGER NOT NULL,
        randomize_questions INTEGER NOT NULL DEFAULT 0,
        randomize_choices INTEGER NOT NULL DEFAULT 0,
        passing_score REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS exam_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id INTEGER NOT NULL REFERENCES exams(id),
        question_key TEXT NOT NULL,
        question_type TEXT NOT NULL,
        question_text TEXT NOT NULL,
        points_possible REAL NOT NULL,
        question_order INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS exam_choices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question_id INTEGER NOT NULL REFERENCES exam_questions(id) ON DELETE CASCADE,
        choice_key TEXT NOT NULL,
        choice_text TEXT NOT NULL,
        is_correct INTEGER NOT NULL DEFAULT 0,
        choice_order INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS exam_attempt_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assessment_attempt_id INTEGER NOT NULL REFERENCES assessment_attempts(id) ON DELETE CASCADE,
        question_id INTEGER NOT NULL REFERENCES exam_questions(id),
        rendered_order INTEGER NOT NULL,
        randomized_seed TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(assessment_attempt_id, question_id)
    );

    COMMIT;
    PRAGMA foreign_keys=ON;
    """)

    conn.close()
    print("Phase 4A migration complete.")

if __name__ == "__main__":
    migrate()
