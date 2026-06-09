import hashlib
import random
from fastapi import HTTPException
from db_adapter import db

def materialize_exam_attempt(attempt_id: int, exam_id: int) -> list:
    conn = db.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        
        # Check if already materialized
        existing = conn.execute("SELECT * FROM exam_attempt_questions WHERE assessment_attempt_id=? ORDER BY rendered_order", (attempt_id,)).fetchall()
        if existing:
            conn.commit()
            return [dict(e) for e in existing]
            
        # Get exam settings
        exam = conn.execute("SELECT * FROM exams WHERE id=?", (exam_id,)).fetchone()
        if not exam:
            raise HTTPException(404, "Exam not found")
            
        # Get all questions
        questions = conn.execute("SELECT id, question_order FROM exam_questions WHERE exam_id=? ORDER BY question_order", (exam_id,)).fetchall()
        
        # Determine order
        q_ids = [q["id"] for q in questions]
        
        # Create deterministic seed
        att = conn.execute("SELECT student_id FROM assessment_attempts WHERE id=?", (attempt_id,)).fetchone()
        seed_str = f"exam_{exam_id}_student_{att['student_id']}_attempt_{attempt_id}"
        seed_val = int(hashlib.md5(seed_str.encode()).hexdigest()[:8], 16)
        
        if exam["randomize_questions"]:
            rng = random.Random(seed_val)
            rng.shuffle(q_ids)
            
        res = []
        for i, q_id in enumerate(q_ids):
            cur = conn.execute(
                "INSERT INTO exam_attempt_questions (assessment_attempt_id, question_id, rendered_order, randomized_seed) VALUES (?, ?, ?, ?)",
                (attempt_id, q_id, i+1, str(seed_val))
            )
            res.append({
                "id": cur.lastrowid,
                "assessment_attempt_id": attempt_id,
                "question_id": q_id,
                "rendered_order": i+1,
                "randomized_seed": str(seed_val)
            })
            
        conn.commit()
        return res
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        db.return_connection(conn)

def get_exam_state(attempt_id: int) -> dict:
    conn = db.get_connection()
    try:
        att = conn.execute("SELECT * FROM assessment_attempts WHERE id=?", (attempt_id,)).fetchone()
        if not att:
            raise HTTPException(404, "Attempt not found")
            
        exam = conn.execute("SELECT * FROM exams WHERE assessment_id=?", (att["assessment_id"],)).fetchone()
        if not exam:
            raise HTTPException(404, "Exam not found")
            
        layout = conn.execute("""
            SELECT eaq.rendered_order, eaq.randomized_seed, eq.question_key, eq.question_text, eq.question_type, eq.id as q_id
            FROM exam_attempt_questions eaq
            JOIN exam_questions eq ON eaq.question_id = eq.id
            WHERE eaq.assessment_attempt_id=?
            ORDER BY eaq.rendered_order
        """, (attempt_id,)).fetchall()
        
        questions_data = []
        for q in layout:
            choices = conn.execute("SELECT choice_key, choice_text FROM exam_choices WHERE question_id=? ORDER BY choice_order", (q["q_id"],)).fetchall()
            choice_list = [dict(c) for c in choices]
            
            if exam["randomize_choices"]:
                # Seed is based on the main seed + question_id
                seed_val = int(q["randomized_seed"])
                rng = random.Random(seed_val + q["q_id"])
                rng.shuffle(choice_list)
                
            questions_data.append({
                "question_key": q["question_key"],
                "question_text": q["question_text"],
                "question_type": q["question_type"],
                "choices": choice_list,
                "rendered_order": q["rendered_order"]
            })
            
        return {
            "attempt_id": attempt_id,
            "session_status": att["session_status"],
            "expires_at": att["expires_at"],
            "questions": questions_data
        }
    finally:
        db.return_connection(conn)
