import json
import logging
import sys
from datetime import datetime

# Configure module logger to emit only JSON to stdout
logger = logging.getLogger("payment_logger")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)

def log_payment_event(
    event: str,
    tx_ref: str,
    student_id: int,
    amount: float,
    flw_ref: str = None,
    webhook_event_id: str = None,
    status: str = None,
    error: str = None
) -> None:
    """
    Emits a deterministic structured JSON log for payment lifecycle events.
    Allowed events:
    - payment_initiated
    - webhook_received
    - payment_verified
    - enrollment_activated
    - payment_failed
    - reconciliation_retry
    """
    allowed_events = {
        "payment_initiated",
        "webhook_received",
        "payment_verified",
        "enrollment_activated",
        "payment_failed",
        "reconciliation_retry",
    }
    if event not in allowed_events:
        raise ValueError(f"Invalid payment event: {event}")

    payload = {
        "event": event,
        "tx_ref": tx_ref,
        "student_id": student_id,
        "amount": amount,
        "flw_ref": flw_ref,
        "webhook_event_id": webhook_event_id,
        "status": status,
        "error": error,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }

    logger.info(json.dumps(payload))
