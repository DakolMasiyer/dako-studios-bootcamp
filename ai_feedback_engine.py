import json
import logging
import os
import time
from datetime import datetime

import httpx

from db_adapter import db

logger = logging.getLogger("ai_feedback_engine")

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
HTTP_TIMEOUT_SECONDS = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "30"))


def _get_gemini_api_key():
    # Google docs recommend GOOGLE_API_KEY taking precedence when both are set.
    return os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return cleaned.strip()


def _extract_json_text(model_text: str) -> dict:
    cleaned = _strip_code_fences(model_text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def _build_prompt(grading_result_row) -> str:
    grading_payload = dict(grading_result_row)
    serialized = json.dumps(grading_payload, indent=2, default=str)
    return (
        "You are writing concise, practical AI feedback for a bootcamp student.\n"
        "Return only valid JSON with these keys:\n"
        "- generated_feedback\n"
        "- strengths_summary\n"
        "- weaknesses_summary\n"
        "- improvement_suggestions\n\n"
        "Guidance:\n"
        "- Be specific to the submission and grading result.\n"
        "- Keep the tone encouraging and direct.\n"
        "- Keep each field short enough for a dashboard card.\n\n"
        "Grading result data:\n"
        f"{serialized}"
    )


def _generate_with_gemini(prompt: str) -> dict:
    api_key = _get_gemini_api_key()
    if not api_key:
        raise RuntimeError("Gemini API key not configured")

    endpoint = GEMINI_API_URL.format(model=DEFAULT_GEMINI_MODEL)
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 512,
            "responseMimeType": "application/json",
        },
    }

    response = httpx.post(
        endpoint,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        json=payload,
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    response_json = response.json()
    candidates = response_json.get("candidates") or []
    for candidate in candidates:
        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        response_text = "".join(
            part.get("text", "") for part in parts if isinstance(part, dict)
        ).strip()
        if response_text:
            return _extract_json_text(response_text)

    raise RuntimeError("Gemini returned no usable content")


def _mock_feedback():
    time.sleep(2)
    return {
        "generated_feedback": "This is a mocked AI feedback response based on your submission.",
        "strengths_summary": "Good effort, well formatted.",
        "weaknesses_summary": "Lacks some depth in certain areas.",
        "improvement_suggestions": "Try to elaborate more on your points.",
        "ai_model_name": "mock_gpt_4",
    }


def generate_feedback(grading_result_id: int):
    """
    Generates AI feedback for a grading result and writes it to ai_feedback.

    Uses Gemini when `GOOGLE_API_KEY` or `GEMINI_API_KEY` is present.
    Falls back to the existing mock response if the key is missing or the
    Gemini request fails.
    """
    conn = db.get_connection()
    try:
        res = conn.execute(
            "SELECT * FROM grading_results WHERE id=?",
            (grading_result_id,),
        ).fetchone()
        if not res:
            raise ValueError(f"Grading result {grading_result_id} not found")
        grading_payload = dict(res)
    except Exception as exc:
        logger.error(
            "Failed to generate AI feedback for grading_result %s: %s",
            grading_result_id,
            exc,
        )
        raise
    finally:
        db.return_connection(conn)

    from assessment_logger import log_assessment_event

    log_assessment_event(
        "feedback_generation_started",
        grading_payload["submission_id"],
        grading_payload["rubric_id"],
        grading_payload["rubric_version"],
    )

    prompt = _build_prompt(grading_payload)
    ai_model_name = DEFAULT_GEMINI_MODEL

    try:
        ai_result = _generate_with_gemini(prompt)
        generated_feedback = ai_result.get("generated_feedback") or ""
        strengths = ai_result.get("strengths_summary") or ""
        weaknesses = ai_result.get("weaknesses_summary") or ""
        improvements = ai_result.get("improvement_suggestions") or ""
        if not any([generated_feedback, strengths, weaknesses, improvements]):
            raise RuntimeError("Gemini returned an empty feedback payload")
    except Exception as gemini_error:
        logger.warning(
            "Gemini feedback generation failed for grading_result %s: %s. Falling back to mock response.",
            grading_result_id,
            gemini_error,
        )
        fallback = _mock_feedback()
        generated_feedback = fallback["generated_feedback"]
        strengths = fallback["strengths_summary"]
        weaknesses = fallback["weaknesses_summary"]
        improvements = fallback["improvement_suggestions"]
        ai_model_name = fallback["ai_model_name"]

    conn = db.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = datetime.utcnow().isoformat()[:19]
        conn.execute(
            """
            INSERT INTO ai_feedback (
                grading_result_id,
                generated_feedback,
                strengths_summary,
                weaknesses_summary,
                improvement_suggestions,
                ai_model_name,
                generated_at,
                feedback_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'visible')
            """,
            (
                grading_result_id,
                generated_feedback,
                strengths,
                weaknesses,
                improvements,
                ai_model_name,
                now,
            ),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        logger.error(
            "Failed to persist AI feedback for grading_result %s: %s",
            grading_result_id,
            exc,
        )
        raise
    finally:
        db.return_connection(conn)

    logger.info("Generated AI feedback for grading_result %s", grading_result_id)
    log_assessment_event(
        "feedback_generation_completed",
        grading_payload["submission_id"],
        grading_payload["rubric_id"],
        grading_payload["rubric_version"],
    )
