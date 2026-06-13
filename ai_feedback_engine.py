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

# Free keys tried in order first; paid key used only when all free keys are exhausted.
_FREE_KEY_NAMES = [
    "GEMINI_API_KEY",
    "GEMINI_API_KEY_2",
    "GEMINI_API_KEY_3",
    "GEMINI_API_KEY_4",
    "GEMINI_API_KEY_5",
    "GEMINI_API_KEY_6",
]
_PAID_KEY_NAME = "GEMINI_PAID_API_KEY"


def _api_key_sequence() -> list[str]:
    """Return all configured Gemini keys: free-tier first, paid last."""
    keys = [os.getenv(k) for k in _FREE_KEY_NAMES if os.getenv(k)]
    paid = os.getenv(_PAID_KEY_NAME)
    if paid:
        keys.append(paid)
    return keys


def _is_rate_limited(exc: httpx.HTTPStatusError) -> bool:
    if exc.response.status_code == 429:
        return True
    if exc.response.status_code == 503:
        body = exc.response.text or ""
        return "RESOURCE_EXHAUSTED" in body or "quota" in body.lower()
    return False


def _call_gemini_once(prompt: str, api_key: str, max_tokens: int = 512, json_mode: bool = True) -> str:
    gen_config: dict = {"temperature": 0.2 if json_mode else 0.4, "maxOutputTokens": max_tokens}
    if json_mode:
        gen_config["responseMimeType"] = "application/json"
    resp = httpx.post(
        GEMINI_API_URL.format(model=DEFAULT_GEMINI_MODEL),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        json={"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen_config},
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    for cand in resp.json().get("candidates") or []:
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
        if text:
            return text
    raise RuntimeError("Gemini returned no usable content")


def _gemini_with_rotation(prompt: str, max_tokens: int = 512, json_mode: bool = True) -> str:
    """Try each key in order (free → paid), rotating on rate-limit/quota errors."""
    keys = _api_key_sequence()
    if not keys:
        raise RuntimeError("No Gemini API keys configured")
    paid_key = os.getenv(_PAID_KEY_NAME)
    last_error: Exception | None = None
    for i, key in enumerate(keys):
        is_paid = key == paid_key
        label = f"key[{i + 1}]/{'paid' if is_paid else 'free'}"
        try:
            text = _call_gemini_once(prompt, key, max_tokens=max_tokens, json_mode=json_mode)
            if is_paid:
                logger.info("[GEMINI_KEY_ROTATION] succeeded on paid key after %d free key(s) exhausted", i)
            return text
        except httpx.HTTPStatusError as exc:
            if _is_rate_limited(exc):
                logger.warning(
                    "[GEMINI_KEY_ROTATION] %s rate-limited (HTTP %s) — trying next key",
                    label, exc.response.status_code,
                )
                last_error = exc
                continue
            raise
    raise RuntimeError(f"All {len(keys)} Gemini key(s) exhausted. Last error: {last_error}")


def call_gemini_text(prompt: str, max_tokens: int = 256) -> str:
    """Plain-text Gemini call with key rotation. Used by the coach AI-suggest endpoint."""
    return _gemini_with_rotation(prompt, max_tokens=max_tokens, json_mode=False)


def _is_local_dev() -> bool:
    # Mirrors bootcamp_app._is_local_dev; duplicated here because importing
    # bootcamp_app would create a circular import via ai_feedback_queue.
    base_url = os.getenv("BASE_URL", "http://localhost:8000")
    return base_url.startswith("http://localhost") or base_url.startswith("http://127.0.0.1")


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
    return _extract_json_text(_gemini_with_rotation(prompt, max_tokens=512, json_mode=True))


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
    feedback_status = "visible"

    try:
        ai_result = _generate_with_gemini(prompt)
        generated_feedback = ai_result.get("generated_feedback") or ""
        strengths = ai_result.get("strengths_summary") or ""
        weaknesses = ai_result.get("weaknesses_summary") or ""
        improvements = ai_result.get("improvement_suggestions") or ""
        if not any([generated_feedback, strengths, weaknesses, improvements]):
            raise RuntimeError("Gemini returned an empty feedback payload")
    except Exception as gemini_error:
        log_assessment_event(
            "feedback_generation_failed",
            grading_payload["submission_id"],
            grading_payload["rubric_id"],
            grading_payload["rubric_version"],
            error=str(gemini_error),
        )
        if not _is_local_dev():
            # Never show students fabricated feedback in production: skip the
            # DB write entirely so the feedback panel stays empty.
            logger.error(
                "Gemini feedback generation failed for grading_result %s in production; no feedback written: %s",
                grading_result_id,
                gemini_error,
            )
            return
        logger.warning(
            "Gemini feedback generation failed for grading_result %s: %s. Falling back to mock response (hidden, local dev only).",
            grading_result_id,
            gemini_error,
        )
        fallback = _mock_feedback()
        generated_feedback = fallback["generated_feedback"]
        strengths = fallback["strengths_summary"]
        weaknesses = fallback["weaknesses_summary"]
        improvements = fallback["improvement_suggestions"]
        ai_model_name = fallback["ai_model_name"]
        feedback_status = "hidden"

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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                grading_result_id,
                generated_feedback,
                strengths,
                weaknesses,
                improvements,
                ai_model_name,
                now,
                feedback_status,
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
