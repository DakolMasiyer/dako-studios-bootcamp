import json
import logging
import sys
from datetime import datetime

# Configure module logger to emit only JSON to stdout
logger = logging.getLogger("submission_logger")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)

def log_submission_event(
    event: str,
    submission_id: int,
    assessment_id: int = None,
    student_id: int = None,
    attempt_number: int = None,
    filename: str = None,
    file_size: int = None,
    mime_type: str = None,
    error: str = None
) -> None:
    """
    Emits a deterministic structured JSON log for submission lifecycle events.
    Allowed events:
    - draft_created
    - upload_started
    - upload_completed
    - upload_failed
    - submission_finalized
    - grading_enqueued
    - finalize_failed
    """
    allowed_events = {
        "draft_created",
        "upload_started",
        "upload_completed",
        "upload_failed",
        "submission_finalized",
        "grading_enqueued",
        "finalize_failed"
    }
    if event not in allowed_events:
        raise ValueError(f"Invalid submission event: {event}")

    payload = {
        "event": event,
        "submission_id": submission_id,
        "assessment_id": assessment_id,
        "student_id": student_id,
        "attempt_number": attempt_number,
        "filename": filename,
        "file_size": file_size,
        "mime_type": mime_type,
        "error": error,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }

    # Remove None values for cleaner logs
    payload = {k: v for k, v in payload.items() if v is not None}

    logger.info(json.dumps(payload))
