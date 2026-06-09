import json
import logging
import queue
import sys
import threading
import time
import uuid
from datetime import datetime

logger = logging.getLogger("assessment_queue")

REDIS_CONN = None
try:
    import redis

    REDIS_CONN = redis.Redis(host="localhost", port=6379, db=0, socket_timeout=2)
    REDIS_CONN.ping()
    logger.info("Connected to Redis for assessment queue.")
except Exception as e:
    logger.warning(f"Redis is not available, falling back to in-memory queue. Error: {e}")
    REDIS_CONN = None

FALLBACK_QUEUE = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()

MAX_RETRIES = 3
LEASE_TIMEOUT_SECONDS = 60
HEARTBEAT_INTERVAL = 15


class _ClaimRejected(Exception):
    pass


def _log_event(event, job_id, submission_id, worker_id, fence_token, retry_count, duration_ms=None, error=None):
    log_payload = {
        "event": event,
        "job_id": str(job_id),
        "submission_id": int(submission_id) if isinstance(submission_id, (int, str)) and str(submission_id).isdigit() else submission_id,
        "worker_id": str(worker_id) if worker_id is not None else None,
        "fence_token": int(fence_token) if fence_token is not None else 0,
        "retry_count": int(retry_count) if retry_count is not None else 0,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "duration_ms": int(duration_ms) if duration_ms is not None else None,
        "error": str(error) if error is not None else None,
    }
    sys.stdout.write(json.dumps(log_payload) + "\n")
    sys.stdout.flush()


def update_heartbeat(job_id, worker_id, fence_token):
    from db_adapter import db

    try:
        with db.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE assessment_jobs SET last_heartbeat_at=? WHERE id=? AND worker_id=? AND fence_token=? AND status='running'",
                (datetime.utcnow().isoformat()[:19], job_id, worker_id, fence_token),
            )
    except Exception as e:
        logger.error(f"Heartbeat update failed for {job_id}: {e}")


def reclaim_orphaned_jobs():
    from db_adapter import db

    try:
        with db.transaction(immediate=True) as conn:
            threshold = datetime.utcnow().timestamp() - LEASE_TIMEOUT_SECONDS
            threshold_iso = datetime.utcfromtimestamp(threshold).isoformat()[:19]
            conn.execute(
                """
                UPDATE assessment_jobs
                SET status='pending', fence_token=fence_token+1, worker_id=NULL, last_heartbeat_at=NULL
                WHERE status='running' AND last_heartbeat_at < ?
                """,
                (threshold_iso,),
            )
    except Exception as e:
        logger.error(f"Reclaim orphans failed: {e}")


def process_job_payload(payload):
    """Executes the grading logic for a single job."""
    from db_adapter import db

    start_time = time.time()
    job_id = payload["job_id"]
    sub_id = payload["submission_id"]
    worker_id = payload.get("worker_id")
    claimed_job_data = None
    stop_heartbeat = threading.Event()

    def _heartbeat_worker():
        while not stop_heartbeat.wait(HEARTBEAT_INTERVAL):
            if claimed_job_data:
                update_heartbeat(job_id, worker_id, claimed_job_data["fence_token"])

    hb_thread = threading.Thread(target=_heartbeat_worker, daemon=True)
    hb_thread.start()

    try:
        with db.transaction(immediate=True) as conn:
            claim_started_at = datetime.utcnow().isoformat()[:19]
            cur = conn.execute(
                "UPDATE assessment_jobs SET status='running', started_at=?, last_heartbeat_at=?, worker_id=?, fence_token=fence_token+1 WHERE id=? AND status='pending'",
                (claim_started_at, claim_started_at, worker_id, job_id),
            )
            if cur.rowcount != 1:
                raise _ClaimRejected(f"Job {job_id} is no longer pending")

            job_data = conn.execute(
                "SELECT fence_token, worker_id, retry_count, submission_id FROM assessment_jobs WHERE id=?",
                (job_id,),
            ).fetchone()

        if not job_data:
            raise ValueError(f"Job {job_id} not found in database")

        claimed_job_data = job_data
        _log_event(
            event="running",
            job_id=job_id,
            submission_id=job_data["submission_id"],
            worker_id=job_data["worker_id"],
            fence_token=job_data["fence_token"],
            retry_count=job_data["retry_count"],
        )

        from rubric_engine import evaluate_submission

        evaluate_submission(sub_id)

        with db.transaction(immediate=True) as conn:
            cur = conn.execute(
                "UPDATE assessment_jobs SET status='completed', completed_at=? WHERE id=? AND worker_id=? AND fence_token=?",
                (datetime.utcnow().isoformat()[:19], job_id, worker_id, claimed_job_data["fence_token"]),
            )
            if cur.rowcount != 1:
                raise _ClaimRejected(f"Stale lease on completion for job {job_id}")

            final_job = conn.execute(
                "SELECT fence_token, worker_id, retry_count, submission_id FROM assessment_jobs WHERE id=?",
                (job_id,),
            ).fetchone()

        duration_ms = int((time.time() - start_time) * 1000)
        _log_event(
            event="completed",
            job_id=job_id,
            submission_id=final_job["submission_id"] if final_job else sub_id,
            worker_id=final_job["worker_id"] if final_job else worker_id,
            fence_token=final_job["fence_token"] if final_job else 0,
            retry_count=final_job["retry_count"] if final_job else 0,
            duration_ms=duration_ms,
        )
    except _ClaimRejected as exc:
        logger.warning(str(exc))
    except Exception as exc:
        logger.error(f"Error processing assessment job {job_id}: {exc}", exc_info=True)

        try:
            job_data = claimed_job_data
            if job_data is None:
                with db.transaction(immediate=True) as conn:
                    job_data = conn.execute(
                        "SELECT fence_token, worker_id, retry_count, submission_id FROM assessment_jobs WHERE id=?",
                        (job_id,),
                    ).fetchone()
        except Exception:
            job_data = None

        curr_retry = job_data["retry_count"] if job_data and job_data["retry_count"] is not None else 0
        next_retry = curr_retry + 1
        is_dead = next_retry >= MAX_RETRIES
        new_status = "dead" if is_dead else "failed"

        try:
            with db.transaction(immediate=True) as conn:
                cur = conn.execute(
                    "UPDATE assessment_jobs SET status=?, retry_count=?, completed_at=?, last_error=?, worker_id=?, fence_token=? WHERE id=? AND worker_id=? AND fence_token=?",
                    (
                        new_status,
                        next_retry,
                        datetime.utcnow().isoformat()[:19],
                        str(exc),
                        job_data["worker_id"] if job_data and job_data["worker_id"] is not None else worker_id,
                        job_data["fence_token"] if job_data and job_data["fence_token"] is not None else 0,
                        job_id,
                        worker_id,
                        claimed_job_data["fence_token"] if claimed_job_data else 0,
                    ),
                )
                if cur.rowcount == 0:
                    logger.warning(f"Stale lease during failure write for job {job_id}")
                    return
        except Exception as update_err:
            logger.error(f"Failed to record failure in DB: {update_err}")

        try:
            with db.transaction() as conn:
                updated_job = conn.execute(
                    "SELECT fence_token, worker_id, retry_count, submission_id FROM assessment_jobs WHERE id=?",
                    (job_id,),
                ).fetchone()
        except Exception:
            updated_job = None

        event_name = "dead" if is_dead else "failed"
        duration_ms = int((time.time() - start_time) * 1000)
        _log_event(
            event=event_name,
            job_id=job_id,
            submission_id=updated_job["submission_id"] if updated_job else sub_id,
            worker_id=updated_job["worker_id"] if updated_job else worker_id,
            fence_token=updated_job["fence_token"] if updated_job else 0,
            retry_count=updated_job["retry_count"] if updated_job else next_retry,
            duration_ms=duration_ms,
            error=str(exc),
        )
    finally:
        stop_heartbeat.set()


def _fallback_worker_loop():
    while True:
        try:
            payload = FALLBACK_QUEUE.get()
            if payload is None:
                break
            payload["worker_id"] = f"fallback-worker-{threading.get_ident()}"
            process_job_payload(payload)
        except Exception as e:
            logger.error(f"Fallback worker encountered error: {e}", exc_info=True)
        finally:
            FALLBACK_QUEUE.task_done()


def ensure_worker_started():
    global _worker_started
    if REDIS_CONN is not None:
        return

    with _worker_lock:
        if not _worker_started:
            t = threading.Thread(target=_fallback_worker_loop, daemon=True, name="FallbackQueueWorker")
            t.start()
            _worker_started = True
            logger.info("Started in-memory fallback queue worker thread.")


def trigger_grading_job(job_id: str, sub_id: int):
    _log_event(
        event="queued",
        job_id=job_id,
        submission_id=sub_id,
        worker_id=None,
        fence_token=0,
        retry_count=0,
    )

    payload = {"job_id": job_id, "submission_id": sub_id}

    if REDIS_CONN is not None:
        try:
            REDIS_CONN.lpush("assessment_queue", json.dumps(payload))
            logger.info(f"Enqueued job {job_id} to Redis.")
        except Exception as e:
            logger.error(f"Redis Lpush failed: {e}. Falling back to in-memory queue.")
            ensure_worker_started()
            FALLBACK_QUEUE.put(payload)
    else:
        ensure_worker_started()
        FALLBACK_QUEUE.put(payload)

    return job_id


def enqueue_grading_job(submission_id: int, verdict=None, feedback=None, reviewer=None):
    from db_adapter import db

    with db.transaction(immediate=True) as conn:
        existing = conn.execute(
            "SELECT id FROM assessment_jobs WHERE submission_id=? AND status IN ('pending', 'running', 'completed')",
            (submission_id,),
        ).fetchone()
        if existing:
            return existing["id"]

        job_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO assessment_jobs (id, submission_id, status) VALUES (?, ?, 'pending')",
            (job_id, submission_id),
        )

    return trigger_grading_job(job_id, submission_id)
