from datetime import datetime
import json
from db_adapter import db
from assessment_logger import log_assessment_event

def _deterministic_evaluate_rule(rule: dict, answer_text: str, has_file: bool) -> float:
    """
    Very basic deterministic logic since no AI allowed yet.
    We just check for basic length or presence.
    """
    if rule["scoring_type"] == "boolean":
        return rule["points_possible"] if answer_text.strip() or has_file else 0.0
    elif rule["scoring_type"] == "length":
        # e.g., 100 points for > 100 chars
        return min(rule["points_possible"], len(answer_text) / 100 * rule["points_possible"])
    return rule["points_possible"]

def evaluate_submission(submission_id: int, override_rubric_id: int = None) -> int:
    """
    Evaluates a submission against its rubric.
    Returns the new grading_result_id.
    """
    conn = db.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")

        # 1. Fetch submission
        sub = conn.execute("SELECT * FROM submissions WHERE id=?", (submission_id,)).fetchone()
        if not sub:
            raise ValueError(f"Submission {submission_id} not found")

        # Prevent duplicate grading if already graded
        if sub["grading_status"] in ("approved", "revision_requested", "completed"):
            existing = conn.execute("SELECT id FROM grading_results WHERE submission_id=? ORDER BY id DESC LIMIT 1", (submission_id,)).fetchone()
            if existing:
                conn.execute("COMMIT")
                return existing["id"]

        # 2. Fetch answer
        ans = conn.execute("SELECT * FROM submission_answers WHERE submission_id=?", (submission_id,)).fetchone()
        answer_text = ans["answer_text"] if ans and ans["answer_text"] else ""
        
        # 3. Check for files
        files = conn.execute("SELECT COUNT(*) as c FROM submission_files WHERE submission_id=?", (submission_id,)).fetchone()
        has_file = files["c"] > 0

        # 4. Fetch rubric
        rubric_id = override_rubric_id or 1 # Fallback to 1 for now if no mapping exists
        rubric = conn.execute("SELECT * FROM rubrics WHERE id=?", (rubric_id,)).fetchone()
        if not rubric:
            raise ValueError(f"Rubric {rubric_id} not found")

        sections = conn.execute("SELECT * FROM rubric_sections WHERE rubric_id=?", (rubric_id,)).fetchall()
        
        total_score = 0.0
        section_results = []

        # 5. Evaluate deterministic math
        for section in sections:
            rules = conn.execute("SELECT * FROM rubric_rules WHERE section_id=?", (section["id"],)).fetchall()
            section_points = 0.0
            max_points = 0.0

            for rule in rules:
                max_points += rule["points_possible"]
                section_points += _deterministic_evaluate_rule(dict(rule), answer_text, has_file)

            # Cap at max_score for the section and apply weight visually or practically
            # For this simple model, max_points dictates the ratio of the section's max_score
            ratio = section_points / max_points if max_points > 0 else 0
            awarded = ratio * section["max_score"]
            total_score += awarded * (section["weight_percentage"] / 100.0)

            section_results.append({
                "section_id": section["id"],
                "awarded": awarded,
                "max_score": section["max_score"],
                "feedback": f"Determined programmatically. Ratio: {ratio:.2f}"
            })

        # 6. Pass/Fail threshold
        passed = total_score >= rubric["pass_threshold"]
        pass_fail_status = "approved" if passed else "revision_requested"
        
        now = datetime.utcnow().isoformat()[:19]

        # 7. Write results transactionally
        cur = conn.execute("""
            INSERT INTO grading_results (submission_id, rubric_id, total_score, pass_fail_status, grading_status, graded_at, rubric_version)
            VALUES (?, ?, ?, ?, 'completed', ?, ?)
        """, (submission_id, rubric_id, total_score, pass_fail_status, now, rubric["rubric_version"]))
        
        grading_result_id = cur.lastrowid

        # 8. Write breakdowns
        for sr in section_results:
            conn.execute("""
                INSERT INTO grading_breakdowns (grading_result_id, section_id, awarded_score, max_score, feedback_text)
                VALUES (?, ?, ?, ?, ?)
            """, (grading_result_id, sr["section_id"], sr["awarded"], sr["max_score"], sr["feedback"]))

        # 9. Update submission status
        conn.execute("UPDATE submissions SET grading_status=?, feedback_summary=?, updated_at=? WHERE id=?", 
            (pass_fail_status, f"Scored {total_score:.1f}/{sum(s['max_score'] * s['weight_percentage']/100.0 for s in sections)}", now, submission_id))

        # 10. Update student progression if approved; capture email data before commit
        _email_student = conn.execute("SELECT name, email, current_day FROM students WHERE id=?", (sub["student_id"],)).fetchone()
        _advanced = False
        if passed and _email_student and _email_student["current_day"] == sub["assessment_id"]:
            conn.execute("UPDATE students SET current_day=current_day+1 WHERE id=?", (sub["student_id"],))
            _advanced = True

        import uuid
        job_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO ai_feedback_jobs (id, grading_result_id) VALUES (?, ?)",
            (job_id, grading_result_id)
        )

        conn.execute("COMMIT")

        # 11. Log natively
        log_assessment_event("rubric_evaluation_completed", submission_id, rubric_id, rubric["rubric_version"], total_score, passed)

        # 12. Send emails after commit so they only fire on persisted state
        if _email_student:
            try:
                from email_service import send_day_passed, send_completion, send_revision_requested
                if _advanced:
                    _next_day = _email_student["current_day"] + 1
                    if _next_day > 20:
                        send_completion(_email_student["name"], _email_student["email"])
                    else:
                        send_day_passed(_email_student["name"], _email_student["email"], _email_student["current_day"], _next_day)
                elif not passed:
                    send_revision_requested(_email_student["name"], _email_student["email"], sub["assessment_id"], "")
            except Exception:
                pass

        from ai_feedback_queue import trigger_feedback_job
        try:
            trigger_feedback_job(job_id, grading_result_id)
        except Exception as e:
            # We don't want to fail grading if queuing fails
            print(f"Failed to queue AI feedback: {e}")
        
        return grading_result_id

    except Exception as e:
        conn.rollback()
        log_assessment_event("rubric_evaluation_failed", submission_id, override_rubric_id, 0, 0, 0, error=str(e))
        raise e
    finally:
        db.return_connection(conn)

def manual_override(grading_result_id: int, new_score: float, new_status: str, reason: str, reviewer: str):
    conn = db.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        
        res = conn.execute("SELECT * FROM grading_results WHERE id=?", (grading_result_id,)).fetchone()
        if not res: raise ValueError("Grading result not found")
        
        # Insert override (append only log)
        conn.execute("""
            INSERT INTO overrides (grading_result_id, new_total_score, new_pass_fail_status, override_reason, reviewer_attribution)
            VALUES (?, ?, ?, ?, ?)
        """, (grading_result_id, new_score, new_status, reason, reviewer))
        
        now = datetime.utcnow().isoformat()[:19]
        # Update submission to reflect the override
        conn.execute("UPDATE submissions SET grading_status=?, feedback_summary=?, updated_at=? WHERE id=?", 
                     (new_status, f"Manually overridden to {new_status} ({new_score} pts). Reason: {reason}", now, res["submission_id"]))
                     
        # If changed to approved, advance student
        _email_data = None
        if new_status in ("approved", "revision_requested"):
            sub = conn.execute("SELECT student_id, assessment_id FROM submissions WHERE id=?", (res["submission_id"],)).fetchone()
            st = conn.execute("SELECT id, current_day, name, email FROM students WHERE id=?", (sub["student_id"],)).fetchone()
            if new_status == "approved" and st and st["current_day"] == sub["assessment_id"]:
                conn.execute("UPDATE students SET current_day=current_day+1 WHERE id=?", (st["id"],))
                _email_data = ("approved", dict(st), sub["assessment_id"])
            elif new_status == "revision_requested" and st:
                _email_data = ("revision", dict(st), sub["assessment_id"])

        conn.execute("COMMIT")

        if _email_data:
            try:
                kind, _st, _day = _email_data
                from email_service import send_day_passed, send_completion, send_revision_requested
                if kind == "approved":
                    _next = _st["current_day"] + 1
                    if _next > 20:
                        send_completion(_st["name"], _st["email"])
                    else:
                        send_day_passed(_st["name"], _st["email"], _day, _next)
                elif kind == "revision":
                    send_revision_requested(_st["name"], _st["email"], _day, reason)
            except Exception:
                pass

        log_assessment_event("manual_score_override", res["submission_id"], res["rubric_id"], res["rubric_version"], new_score, new_status=='approved', reviewer=reviewer)
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        db.return_connection(conn)
