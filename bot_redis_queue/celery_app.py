"""Celery application configuration."""

import os
from celery import Celery
from .constants import QueueNames

# Get Redis URL from environment or use default
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Create Celery app
celery_app = Celery(
    "github_bot",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["bot_redis_queue.tasks"]
)

# Configure Celery
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_routes={
        "bot_redis_queue.tasks.process_github_event": {"queue": QueueNames.GITHUB_EVENTS},
        "bot_redis_queue.tasks.generate_jobs": {"queue": QueueNames.JOB_GENERATION},
        "bot_redis_queue.tasks.check_job_status": {"queue": QueueNames.MONITORING},
        "bot_redis_queue.tasks.parse_logs_and_post_status": {"queue": QueueNames.RESULTS},
    },
)
