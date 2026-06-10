import json
import logging
import sys
from datetime import datetime

# Configure module logger to emit only JSON to stdout
logger = logging.getLogger("assessment_logger")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)

def log_assessment_event(
    event: str,
    submission_id: int,
    rubric_id: int,
    rubric_version: int,
    total_score: float = None,
    passed: bool = None,
    reviewer: str = None,
    error: str = None
) -> None:
    """
    Emits a deterministic structured JSON log for grading assessment events.
    Allowed events:
    - rubric_evaluation_started
    - rubric_evaluation_completed
    - rubric_evaluation_failed
    - manual_score_override
    - feedback_generation_started
    - feedback_generation_completed
    - feedback_hidden
    - feedback_regenerated
    """
    allowed_events = {
        "rubric_evaluation_started",
        "rubric_evaluation_completed",
        "rubric_evaluation_failed",
        "manual_score_override",
        "feedback_generation_started",
        "feedback_generation_completed",
        "feedback_generation_failed",
        "feedback_hidden",
        "feedback_regenerated",
        "session_started",
        "session_autosaved",
        "session_expired",
        "session_submitted",
        "session_locked",
        "exam_started",
        "exam_question_rendered",
        "exam_answer_saved",
        "exam_submitted"
    }
    if event not in allowed_events:
        raise ValueError(f"Invalid assessment event: {event}")

    payload = {
        "event": event,
        "submission_id": submission_id,
        "rubric_id": rubric_id,
        "rubric_version": rubric_version,
        "total_score": total_score,
        "passed": passed,
        "reviewer": reviewer,
        "error": error,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }

    # Remove None values for cleaner logs
    payload = {k: v for k, v in payload.items() if v is not None}

    logger.info(json.dumps(payload))
