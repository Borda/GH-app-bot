"""Constants and enums used throughout the GitHub bot application."""

from enum import Enum


class TaskStatus(str, Enum):
    """Task execution status enum to prevent typos and ensure consistency."""
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"
    IGNORED = "ignored"
    ERROR = "error"


# Queue names as constants to prevent typos
class QueueNames:
    """Celery queue names for task routing."""
    GITHUB_EVENTS = "github_events"
    JOB_GENERATION = "job_generation"
    MONITORING = "monitoring"
    RESULTS = "results"
