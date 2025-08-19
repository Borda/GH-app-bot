from enum import Enum


class TaskType(Enum):
    """Task types"""

    NEW_EVENT = "new_event"
    START_JOB = "start_job"
    WAIT_JOB = "wait_job"
    RESULTS = "results"
