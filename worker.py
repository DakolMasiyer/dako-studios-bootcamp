#!/usr/bin/env python3
import time
import json
import logging
import sys
import socket
import os
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("worker")

# Add project root to path
sys.path.append(str(Path(__file__).parent))

from assessment_queue import REDIS_CONN, process_job_payload, reclaim_orphaned_jobs

WORKER_ID = f"worker-{socket.gethostname()}-{os.getpid()}"

def main():
    if REDIS_CONN is None:
        logger.error("Redis connection is unavailable. Standalone worker cannot start. Exiting.")
        sys.exit(1)
        
    logger.info(f"Standalone Redis worker {WORKER_ID} started. Listening on 'assessment_queue'...")
    last_reclaim_time = time.time()
    
    while True:
        try:
            # Periodically sweep orphans (e.g., every 30 seconds)
            now = time.time()
            if now - last_reclaim_time > 30:
                reclaim_orphaned_jobs()
                last_reclaim_time = now

            res = REDIS_CONN.blpop("assessment_queue", timeout=5)
            if res:
                _, val = res
                payload = json.loads(val.decode("utf-8"))
                payload["worker_id"] = WORKER_ID
                logger.info(f"Retrieved job {payload.get('job_id')} from Redis.")
                process_job_payload(payload)
        except KeyboardInterrupt:
            logger.info("Shutting down worker...")
            break
        except Exception as e:
            logger.error(f"Worker main loop error: {e}", exc_info=True)
            time.sleep(2)

if __name__ == "__main__":
    main()
