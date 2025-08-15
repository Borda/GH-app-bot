"""Celery worker script for processing GitHub events."""

import logging
import os
from .celery_app import celery_app

# Configure logging for worker
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info("Starting Celery worker for GitHub bot")

    # Get worker configuration from environment
    concurrency = int(os.getenv("WORKER_CONCURRENCY", "4"))
    queues = os.getenv("WORKER_QUEUES", "github_events,downloads,job_generation,monitoring,results")
    log_level = os.getenv("WORKER_LOG_LEVEL", "info")

    logger.info(f"Worker configuration:")
    logger.info(f"  Concurrency: {concurrency}")
    logger.info(f"  Queues: {queues}")
    logger.info(f"  Log level: {log_level}")

    # Start worker
    celery_app.worker_main([
        "worker",
        f"--concurrency={concurrency}",
        f"--queues={queues}",
        f"--loglevel={log_level}",
        "--without-gossip",
        "--without-mingle",
        "--without-heartbeat",
    ])
