from typing import Optional
from datetime import datetime
import json
import os
import secrets
from pathlib import Path

from fastapi import UploadFile, HTTPException

from bootcamp_app import UPLOADS
from db_adapter import db
from submission_logger import log_submission_event

try:
    from vercel.blob import AsyncBlobClient
except Exception:  # pragma: no cover - optional production dependency
    AsyncBlobClient = None

ALLOWED_MIMES = {"image/png", "image/jpeg", "application/pdf"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB


def _using_blob_storage() -> bool:
    return bool(os.getenv("BLOB_READ_WRITE_TOKEN")) and AsyncBlobClient is not None

def get_or_create_draft(student_id: int, assessment_id: int) -> dict:
    try:
        with db.transaction(immediate=True) as conn:
            # Check max attempts
            assessment = conn.execute("SELECT max_attempts FROM assessments WHERE id=?", (assessment_id,)).fetchone()
            if not assessment:
                raise HTTPException(404, "Assessment not found")
                
            draft = conn.execute(
                "SELECT * FROM submissions WHERE student_id=? AND assessment_id=? AND submission_status='draft'",
                (student_id, assessment_id)
            ).fetchone()
            
            if draft:
                return dict(draft)
                
            # Check how many non-draft attempts exist
            attempts = conn.execute(
                "SELECT COUNT(*) as c FROM submissions WHERE student_id=? AND assessment_id=? AND submission_status != 'draft'",
                (student_id, assessment_id)
            ).fetchone()["c"]
            
            if attempts >= assessment["max_attempts"]:
                raise HTTPException(403, f"Max attempts ({assessment['max_attempts']}) reached")
                
            attempt_number = attempts + 1
            
            cur = conn.execute(
                "INSERT INTO submissions (assessment_id, student_id, submission_status, attempt_number) VALUES (?, ?, ?, ?)",
                (assessment_id, student_id, 'draft', attempt_number)
            )
            
            # For Postgres, cur doesn't have lastrowid easily accessible if not using RETURNING.
            # wait, I added lastrowid fallback in PostgresAdapter for run(), but here we call conn.execute.
            # In db_adapter.py we wrapped conn, let's see if we can get the ID via RETURNING if Postgres.
            # For now, since SQLite gives us lastrowid on cur, let's use it, but wrapped execute doesn't return cur, it returns raw cur.
            # So cur.lastrowid will exist on SQLite. What about Postgres? We'll have an issue there.
            # Better to use `db.run` for INSERTs, or select MAX(id) in the same transaction.
            # Let's do SELECT id from submissions where student_id=? and assessment_id=? and submission_status='draft'
            # since we just inserted it.
            
            sub = conn.execute(
                "SELECT * FROM submissions WHERE student_id=? AND assessment_id=? AND submission_status='draft'",
                (student_id, assessment_id)
            ).fetchone()
            sub_id = sub["id"]
            
            log_submission_event("draft_created", sub_id, assessment_id, student_id, attempt_number)
            return dict(sub)
    except HTTPException:
        raise
    except Exception as e:
        raise e

def save_structured_answer(submission_id: int, question_key: str, text: str, json_data: str = None) -> None:
    try:
        with db.transaction(immediate=True) as conn:
            sub = conn.execute("SELECT submission_status FROM submissions WHERE id=?", (submission_id,)).fetchone()
            if not sub or sub["submission_status"] != "draft":
                raise HTTPException(400, "Can only save answers to drafts")
                
            existing = conn.execute("SELECT id FROM submission_answers WHERE submission_id=? AND question_key=?", (submission_id, question_key)).fetchone()
            if existing:
                conn.execute("UPDATE submission_answers SET answer_text=?, answer_json=? WHERE id=?", (text, json_data, existing["id"]))
            else:
                conn.execute("INSERT INTO submission_answers (submission_id, question_key, answer_text, answer_json) VALUES (?, ?, ?, ?)",
                             (submission_id, question_key, text, json_data))
    except HTTPException:
        raise
    except Exception as e:
        raise e

async def process_safe_upload(submission_id: int, file: UploadFile) -> None:
    if not file or not file.filename:
        return
        
    try:
        with db.transaction() as conn:
            sub = conn.execute("SELECT student_id, assessment_id, submission_status FROM submissions WHERE id=?", (submission_id,)).fetchone()
        
        if not sub or sub["submission_status"] != "draft":
            raise HTTPException(400, "Can only upload files to drafts")
            
        student_id = sub["student_id"]
        
        # MIME validation
        mime_type = file.content_type
        if mime_type not in ALLOWED_MIMES:
            log_submission_event("upload_failed", submission_id, error=f"Invalid MIME type: {mime_type}")
            raise HTTPException(400, f"Invalid file type {mime_type}. Allowed: {ALLOWED_MIMES}")
            
        log_submission_event("upload_started", submission_id, filename=file.filename, mime_type=mime_type)

        ext = Path(file.filename).suffix or ".bin"
        stored_path = f"submissions/{student_id}/{sub['assessment_id']}/{submission_id}/{secrets.token_hex(6)}{ext}"

        # Stream read to enforce size limit and save safely
        size = 0
        chunks = []
        while chunk := await file.read(8192):
            size += len(chunk)
            if size > MAX_FILE_SIZE:
                log_submission_event("upload_failed", submission_id, error="File too large")
                raise HTTPException(400, f"File size exceeds limit of {MAX_FILE_SIZE} bytes")
            chunks.append(chunk)

        payload = b"".join(chunks)

        if _using_blob_storage():
            client = AsyncBlobClient()
            await client.put(
                stored_path,
                payload,
                access="private",
                content_type=mime_type,
                add_random_suffix=False,
            )
        else:
            full_path = UPLOADS / stored_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            with open(full_path, "wb") as out:
                out.write(payload)
                
        # Persist metadata
        with db.transaction(immediate=True) as conn:
            conn.execute("""
                INSERT INTO submission_files (submission_id, original_filename, stored_path, mime_type, file_size)
                VALUES (?, ?, ?, ?, ?)
            """, (submission_id, file.filename, stored_path, mime_type, size))
        
        log_submission_event("upload_completed", submission_id, filename=file.filename, file_size=size, mime_type=mime_type)
        
    except HTTPException:
        raise
    except Exception as e:
        raise e

def finalize_submission(submission_id: int) -> None:
    try:
        with db.transaction(immediate=True) as conn:
            sub = conn.execute("SELECT * FROM submissions WHERE id=?", (submission_id,)).fetchone()
            if not sub:
                raise HTTPException(404, "Submission not found")
                
            if sub["submission_status"] == "submitted" or sub["submission_status"] == "queued":
                # Idempotent return
                return
                
            if sub["submission_status"] != "draft":
                raise HTTPException(400, f"Cannot finalize submission in state {sub['submission_status']}")
                
            now = datetime.utcnow().isoformat()[:19]
            conn.execute("UPDATE submissions SET submission_status='submitted', submitted_at=?, updated_at=? WHERE id=?",
                         (now, now, submission_id))
                         
            # Enqueue grading safely inside transaction
            # Check if job exists
            existing_job = conn.execute("SELECT id FROM assessment_jobs WHERE submission_id=?", (submission_id,)).fetchone()
            job_id = existing_job["id"] if existing_job else secrets.token_hex(8)
            
            if not existing_job:
                conn.execute("INSERT INTO assessment_jobs (id, submission_id, status) VALUES (?, ?, 'pending')", (job_id, submission_id))
                
        # Trigger the queue explicitly outside transaction
        if not existing_job:
            from assessment_queue import trigger_grading_job
            trigger_grading_job(job_id, submission_id)
        
        log_submission_event("submission_finalized", submission_id, assessment_id=sub["assessment_id"], student_id=sub["student_id"])
        log_submission_event("grading_enqueued", submission_id, assessment_id=sub["assessment_id"])
        
    except HTTPException:
        raise
    except Exception as e:
        log_submission_event("finalize_failed", submission_id, error=str(e))
        raise e
