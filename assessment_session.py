from datetime import datetime, timedelta
from fastapi import HTTPException
from db_adapter import db
from assessment_logger import log_assessment_event

def get_active_attempt(student_id: int, assessment_id: int) -> dict:
    conn = db.get_connection()
    try:
        return dict(conn.execute(
            "SELECT * FROM assessment_attempts WHERE student_id=? AND assessment_id=? AND session_status='active'",
            (student_id, assessment_id)
        ).fetchone() or {})
    finally:
        db.return_connection(conn)

def sweep_expired_attempts():
    conn = db.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = datetime.utcnow().isoformat()[:19]
        
        expired = conn.execute(
            "SELECT id, assessment_id, submission_id FROM assessment_attempts WHERE session_status='active' AND expires_at < ?", 
            (now,)
        ).fetchall()
        
        for exp in expired:
            conn.execute("UPDATE assessment_attempts SET session_status='expired' WHERE id=?", (exp["id"],))
            log_assessment_event("session_expired", exp["submission_id"], exp["assessment_id"], 1)
            
        conn.commit()
    except Exception as e:
        conn.rollback()
    finally:
        db.return_connection(conn)

def start_attempt(student_id: int, assessment_id: int, time_limit_sec: int = None) -> dict:
    # First, run a sweep to ensure we don't block on an already expired session
    sweep_expired_attempts()
    
    conn = db.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        
        # Enforce max attempts
        assessment = conn.execute("SELECT max_attempts FROM assessments WHERE id=?", (assessment_id,)).fetchone()
        if not assessment:
            raise HTTPException(404, "Assessment not found")
            
        # Count non-abandoned attempts
        attempts = conn.execute(
            "SELECT COUNT(*) as c FROM assessment_attempts WHERE student_id=? AND assessment_id=? AND session_status != 'abandoned'",
            (student_id, assessment_id)
        ).fetchone()["c"]
        
        # Check active session
        active = conn.execute(
            "SELECT * FROM assessment_attempts WHERE student_id=? AND assessment_id=? AND session_status='active'",
            (student_id, assessment_id)
        ).fetchone()
        
        if active:
            conn.execute("COMMIT")
            return dict(active)
            
        if attempts >= assessment["max_attempts"]:
            raise HTTPException(403, f"Max attempts ({assessment['max_attempts']}) reached")
            
        attempt_number = attempts + 1
        now = datetime.utcnow()
        expires_at = (now + timedelta(seconds=time_limit_sec)).isoformat()[:19] if time_limit_sec else None
        now_str = now.isoformat()[:19]
        
        # Create submission draft to associate
        cur = conn.execute(
            "INSERT INTO submissions (assessment_id, student_id, submission_status, attempt_number) VALUES (?, ?, ?, ?)",
            (assessment_id, student_id, 'draft', attempt_number)
        )
        sub_id = cur.lastrowid
        
        # Create attempt
        cur2 = conn.execute(
            "INSERT INTO assessment_attempts (assessment_id, student_id, submission_id, attempt_number, session_status, started_at, expires_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
            (assessment_id, student_id, sub_id, attempt_number, now_str, expires_at)
        )
        attempt_id = cur2.lastrowid
        
        conn.commit()
        
        log_assessment_event("session_started", sub_id, assessment_id, 1)
        
        return dict(conn.execute("SELECT * FROM assessment_attempts WHERE id=?", (attempt_id,)).fetchone())
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        db.return_connection(conn)

def autosave_attempt(attempt_id: int, question_key: str = 'legacy_answer', answer_text: str = None, answer_json: str = None):
    # Enforce sweep before autosave
    sweep_expired_attempts()
    
    conn = db.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        
        att = conn.execute("SELECT * FROM assessment_attempts WHERE id=?", (attempt_id,)).fetchone()
        if not att:
            raise HTTPException(404, "Attempt not found")
        if att["session_status"] != "active":
            raise HTTPException(403, f"Cannot autosave to session in state: {att['session_status']}")
            
        now = datetime.utcnow().isoformat()[:19]
        conn.execute("UPDATE assessment_attempts SET autosave_at=? WHERE id=?", (now, attempt_id))
        
        sub_id = att["submission_id"]
        # Save answers to submission
        if answer_text or answer_json:
            existing = conn.execute("SELECT id FROM submission_answers WHERE submission_id=? AND question_key=?", (sub_id, question_key)).fetchone()
            if existing:
                conn.execute("UPDATE submission_answers SET answer_text=?, answer_json=? WHERE id=?", (answer_text, answer_json, existing["id"]))
            else:
                conn.execute("INSERT INTO submission_answers (submission_id, question_key, answer_text, answer_json) VALUES (?, ?, ?, ?)",
                             (sub_id, question_key, answer_text, answer_json))
                             
        conn.commit()
        log_assessment_event("session_autosaved", sub_id, att["assessment_id"], 1)
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        db.return_connection(conn)

def finalize_attempt(attempt_id: int):
    sweep_expired_attempts()
    
    conn = db.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        
        att = conn.execute("SELECT * FROM assessment_attempts WHERE id=?", (attempt_id,)).fetchone()
        if not att:
            raise HTTPException(404, "Attempt not found")
        if att["session_status"] == "submitted" or att["session_status"] == "locked":
            conn.execute("COMMIT")
            return
        if att["session_status"] != "active":
            raise HTTPException(403, f"Cannot finalize session in state: {att['session_status']}")
            
        now = datetime.utcnow().isoformat()[:19]
        conn.execute("UPDATE assessment_attempts SET session_status='submitted', submitted_at=? WHERE id=?", (now, attempt_id))
        
        # Also finalize submission
        sub_id = att["submission_id"]
        conn.execute("UPDATE submissions SET submission_status='submitted', submitted_at=?, updated_at=? WHERE id=?",
                     (now, now, sub_id))
                     
        conn.commit()
        log_assessment_event("session_submitted", sub_id, att["assessment_id"], 1)
        log_assessment_event("session_locked", sub_id, att["assessment_id"], 1)
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        db.return_connection(conn)
