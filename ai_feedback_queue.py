import uuid
import json
import logging
import threading
import sys
import time
from datetime import datetime
import queue

logger = logging.getLogger("ai_feedback_queue")

REDIS_CONN = None
try:
    import redis
    REDIS_CONN = redis.Redis(host="localhost", port=6379, db=0, socket_timeout=2)
    REDIS_CONN.ping()
except Exception:
    REDIS_CONN = None

FALLBACK_QUEUE = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()

MAX_RETRIES = 3
LEASE_TIMEOUT_SECONDS = 60
HEARTBEAT_INTERVAL = 15

class _ClaimRejected(Exception):
    pass

def _log_event(event, job_id, grading_result_id, worker_id, fence_token, retry_count, duration_ms=None, error=None):
    log_payload = {
        "event": event,
        "job_id": str(job_id),
        "grading_result_id": grading_result_id,
        "worker_id": str(worker_id) if worker_id is not None else None,
        "fence_token": fence_token,
        "retry_count": retry_count,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "duration_ms": duration_ms,
        "error": str(error) if error is not None else None
    }
    sys.stdout.write(json.dumps(log_payload) + "\n")
    sys.stdout.flush()

def update_heartbeat(job_id, worker_id, fence_token):
    from db_adapter import db
    try:
        with db.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE ai_feedback_jobs SET last_heartbeat_at=? WHERE id=? AND worker_id=? AND fence_token=? AND status='running'",
                (datetime.utcnow().isoformat()[:19], job_id, worker_id, fence_token)
            )
    except Exception:
        pass

def process_job_payload(payload):
    from db_adapter import db
    start_time = time.time()
    
    job_id = payload["job_id"]
    gr_id = payload["grading_result_id"]
    worker_id = payload.get("worker_id", f"fallback-{threading.get_ident()}")
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
                "UPDATE ai_feedback_jobs SET status='running', started_at=?, last_heartbeat_at=?, worker_id=?, fence_token=fence_token+1 WHERE id=? AND status='pending'",
                (claim_started_at, claim_started_at, worker_id, job_id)
            )
            if cur.rowcount != 1:
                raise _ClaimRejected(f"Job {job_id} is no longer pending")

            job_data = conn.execute("SELECT fence_token, worker_id, retry_count, grading_result_id FROM ai_feedback_jobs WHERE id=?", (job_id,)).fetchone()
            claimed_job_data = job_data
        
        _log_event("running", job_id, gr_id, worker_id, job_data["fence_token"], job_data["retry_count"])

        from ai_feedback_engine import generate_feedback
        generate_feedback(gr_id)

        with db.transaction(immediate=True) as conn:
            cur = conn.execute(
                "UPDATE ai_feedback_jobs SET status='completed', completed_at=? WHERE id=? AND worker_id=? AND fence_token=?",
                (datetime.utcnow().isoformat()[:19], job_id, worker_id, claimed_job_data["fence_token"])
            )
            if cur.rowcount != 1:
                raise _ClaimRejected("Stale lease on completion")
        
        duration_ms = int((time.time() - start_time) * 1000)
        _log_event("completed", job_id, gr_id, worker_id, claimed_job_data["fence_token"], claimed_job_data["retry_count"], duration_ms)

    except Exception as exc:
        next_retry = 1
        fence_token = 0
        if claimed_job_data:
            next_retry = claimed_job_data["retry_count"] + 1
            fence_token = claimed_job_data["fence_token"]
            
        is_dead = next_retry >= MAX_RETRIES
        new_status = "dead" if is_dead else "failed"
        
        try:
            with db.transaction(immediate=True) as conn:
                conn.execute(
                    "UPDATE ai_feedback_jobs SET status=?, retry_count=?, completed_at=?, last_error=?, worker_id=?, fence_token=? WHERE id=? AND worker_id=? AND fence_token=?",
                    (new_status, next_retry, datetime.utcnow().isoformat()[:19], str(exc), worker_id, fence_token, job_id, worker_id, fence_token)
                )
        except Exception:
            pass
            
        _log_event("failed", job_id, gr_id, worker_id, fence_token, next_retry, error=str(exc))
    finally:
        stop_heartbeat.set()

def _fallback_worker_loop():
    while True:
        try:
            payload = FALLBACK_QUEUE.get()
            if payload is None: break
            payload["worker_id"] = f"fallback-worker-{threading.get_ident()}"
            process_job_payload(payload)
        except Exception:
            pass
        finally:
            FALLBACK_QUEUE.task_done()

def ensure_worker_started():
    global _worker_started
    if REDIS_CONN is not None: return
    with _worker_lock:
        if not _worker_started:
            t = threading.Thread(target=_fallback_worker_loop, daemon=True)
            t.start()
            _worker_started = True

def trigger_feedback_job(job_id: str, gr_id: int):
    payload = {"job_id": job_id, "grading_result_id": gr_id}
    if REDIS_CONN is not None:
        try:
            REDIS_CONN.lpush("ai_feedback_queue", json.dumps(payload))
        except Exception:
            ensure_worker_started()
            FALLBACK_QUEUE.put(payload)
    else:
        ensure_worker_started()
        FALLBACK_QUEUE.put(payload)
    return job_id
