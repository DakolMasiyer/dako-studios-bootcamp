#!/usr/bin/env python3
import sqlite3
from pathlib import Path

DB_PATH = Path("data/bootcamp.db")

def migrate():
    print("Starting Phase 4 (Submission Pipeline) migration...")
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    cur = conn.cursor()

    # Drop v2 if exists from previous failed run
    cur.executescript("""
    PRAGMA foreign_keys=OFF;
    DROP TABLE IF EXISTS submissions_v2;
    """)

    cur.executescript("""
    BEGIN TRANSACTION;

    CREATE TABLE IF NOT EXISTS assessments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        assessment_type TEXT NOT NULL,
        cohort_id INTEGER REFERENCES cohorts(id),
        rubric_id INTEGER REFERENCES rubrics(id),
        max_attempts INTEGER NOT NULL DEFAULT 1,
        opens_at TEXT,
        closes_at TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS submissions_v2 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        assessment_id INTEGER NOT NULL REFERENCES assessments(id),
        student_id INTEGER NOT NULL REFERENCES students(id),
        submission_status TEXT NOT NULL DEFAULT 'draft',
        attempt_number INTEGER NOT NULL DEFAULT 1,
        submitted_at TEXT,
        grading_status TEXT NOT NULL DEFAULT 'pending',
        final_score REAL,
        feedback_summary TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS submission_answers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL REFERENCES submissions_v2(id) ON DELETE CASCADE,
        question_key TEXT NOT NULL,
        answer_text TEXT,
        answer_json TEXT,
        uploaded_file_path TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS submission_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL REFERENCES submissions_v2(id) ON DELETE CASCADE,
        original_filename TEXT NOT NULL,
        stored_path TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        file_size INTEGER NOT NULL,
        uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    COMMIT;
    """)
    
    # Try copying data
    try:
        cur.executescript("""
        BEGIN TRANSACTION;
        INSERT INTO assessments (id, title, description, assessment_type, max_attempts) 
        SELECT 1, 'Legacy Day 1', 'Legacy', 'assignment', 3 
        WHERE NOT EXISTS (SELECT 1 FROM assessments WHERE id=1);

        INSERT INTO submissions_v2 (id, assessment_id, student_id, submission_status, submitted_at, grading_status, feedback_summary, created_at)
        SELECT id, 1, student_id, 'graded', graded_at, status, feedback, submitted_at
        FROM submissions;
        
        INSERT INTO submission_answers (submission_id, question_key, answer_text)
        SELECT id, 'legacy_answer', answer_text
        FROM submissions;
        
        DROP TABLE submissions;
        ALTER TABLE submissions_v2 RENAME TO submissions;
        COMMIT;
        """)
    except Exception as e:
        conn.rollback()
        print(f"Skipping old data copy or already migrated: {e}")

    cur.executescript("PRAGMA foreign_keys=ON;")
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
