"""Celery tasks for processing GitHub events."""

import logging
from typing import Any, Dict

from celery import Task
from .celery_app import celery_app
from .constants import TaskStatus

logger = logging.getLogger(__name__)


class CallbackTask(Task):
    """Base task with error handling."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(f"Task {task_id} failed: {exc}")
        logger.error(f"Task info: {einfo}")


@celery_app.task(base=CallbackTask, bind=True)
def process_github_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
    """Process incoming GitHub webhook event."""
    logger.info(f"Processing GitHub event: {event_data.get('action', 'unknown')} "
                f"for {event_data.get('event_type', 'unknown')}")

    event_type = event_data.get("event_type")
    event_action = event_data.get("action")

    # Route to specific handlers based on event type
    if event_type == "push":
        return handle_push_event.delay(event_data).get()
    elif event_type == "pull_request" and event_action in ["opened", "synchronize", "reopened"]:
        return handle_pull_request_event.delay(event_data).get()
    else:
        logger.info(f"Ignoring event type: {event_type} with action: {event_action}")
        return {"status": TaskStatus.IGNORED, "event_type": event_type, "action": event_action}


@celery_app.task(base=CallbackTask)
def handle_push_event(event_data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle push events."""
    logger.info("Handling push event")
    repo_url = event_data.get("repository", {}).get("clone_url")
    commit_sha = event_data.get("after")

    if repo_url and commit_sha:
        # Directly generate jobs (which will download config files as needed)
        job_result = generate_jobs.delay(repo_url, commit_sha)
        return {"status": TaskStatus.QUEUED, "job_generation_task_id": job_result.id}

    return {"status": TaskStatus.ERROR, "message": "Missing repository URL or commit SHA"}


@celery_app.task(base=CallbackTask)
def handle_pull_request_event(event_data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle pull request events."""
    logger.info("Handling pull request event")
    pr_data = event_data.get("pull_request", {})
    repo_url = pr_data.get("head", {}).get("repo", {}).get("clone_url")
    commit_sha = pr_data.get("head", {}).get("sha")

    if repo_url and commit_sha:
        # Directly generate jobs (which will download config files as needed)
        job_result = generate_jobs.delay(repo_url, commit_sha)
        return {"status": TaskStatus.QUEUED, "job_generation_task_id": job_result.id}

    return {"status": TaskStatus.ERROR, "message": "Missing repository URL or commit SHA"}


@celery_app.task(base=CallbackTask, bind=True)
def generate_jobs(self, repo_url: str, commit_sha: str) -> Dict[str, Any]:
    """Generate jobs based on repository configuration.

    This task will:
    1. Download only the configuration files needed (e.g., .github/workflows/, .ci.yml)
    2. Parse the configuration to determine what jobs to create
    3. Create the jobs using Lightning SDK
    4. Start monitoring the created jobs
    """
    logger.info(f"Generating jobs for repository: {repo_url} at {commit_sha}")

    # TODO: Implement configuration file download logic
    # This should download only specific config files, not the entire repository:
    # - .github/workflows/*.yml (for GitHub Actions workflows)
    # - .ci.yml or similar CI configuration files
    # - lightning.yaml or similar Lightning-specific configs

    # Simulate config download and parsing
    import time
    time.sleep(1)  # Simulate config download time

    logger.info("Downloaded and parsed configuration files")

    # TODO: Parse configuration files and create actual jobs using Lightning SDK
    # This is where we'd:
    # 1. Parse YAML/JSON configuration files
    # 2. Create Lightning jobs based on the configuration
    # 3. Get real job IDs from Lightning SDK

    # Mock job creation for now
    job_ids = ["lightning_job_1", "lightning_job_2", "lightning_job_3"]
    logger.info(f"Created {len(job_ids)} jobs: {job_ids}")

    # Record job creation time for timeout tracking
    import datetime
    job_created_at = datetime.datetime.utcnow().isoformat()

    # Start monitoring each created job with creation timestamp
    for job_id in job_ids:
        check_job_status.delay(job_id, repo_url, commit_sha, job_created_at)

    return {
        "status": TaskStatus.COMPLETED,
        "repo_url": repo_url,
        "commit_sha": commit_sha,
        "job_ids": job_ids,
        "job_created_at": job_created_at,
        "config_downloaded": True,
        "jobs_created": len(job_ids)
    }


@celery_app.task(base=CallbackTask, bind=True)
def check_job_status(self, job_id: str, repo_url: str, commit_sha: str, job_created_at: str) -> Dict[str, Any]:
    """Check job status and trigger log parsing when completed.

    Args:
        job_id: Lightning SDK job ID
        repo_url: Repository URL
        commit_sha: Commit SHA
        job_created_at: ISO timestamp when job was created
    """
    logger.info(f"Checking status for job: {job_id}")

    # Check for job timeout
    import datetime
    created_time = datetime.datetime.fromisoformat(job_created_at)
    current_time = datetime.datetime.utcnow()
    elapsed_minutes = (current_time - created_time).total_seconds() / 60

    # Default timeout: 60 minutes (can be made configurable)
    job_timeout_minutes = 60

    if elapsed_minutes > job_timeout_minutes:
        logger.warning(f"Job {job_id} timed out after {elapsed_minutes:.1f} minutes")
        # TODO: Cancel the job in Lightning SDK
        parse_result = parse_logs_and_post_status.delay(job_id, repo_url, commit_sha, "timeout")
        return {
            "status": TaskStatus.COMPLETED,
            "job_id": job_id,
            "job_status": "timeout",
            "elapsed_minutes": elapsed_minutes,
            "parse_task_id": parse_result.id
        }

    # TODO: Get actual job status from Lightning SDK
    # For now, mock job status check
    import time
    import random
    time.sleep(random.randint(1, 3))  # Simulate API call time

    # Simulate job status (will use Lightning SDK job status)
    job_status = "completed"  # In real implementation: lightning_sdk.get_job_status(job_id)

    if job_status in ["completed", "success", "failed", "cancelled", "timeout"]:
        # Job finished - trigger log parsing and status posting
        logger.info(f"Job {job_id} finished with status: {job_status}")
        parse_result = parse_logs_and_post_status.delay(job_id, repo_url, commit_sha, job_status)
        return {
            "status": TaskStatus.COMPLETED,
            "job_id": job_id,
            "job_status": job_status,
            "elapsed_minutes": elapsed_minutes,
            "parse_task_id": parse_result.id
        }
    else:
        # Job still running - reschedule check with minimum 10 second delay
        logger.info(f"Job {job_id} still running, will check again in 10 seconds")
        check_job_status.apply_async(
            args=[job_id, repo_url, commit_sha, job_created_at], 
            countdown=10  # Wait at least 10 seconds before next check
        )
        return {
            "status": TaskStatus.RUNNING, 
            "job_id": job_id,
            "job_status": job_status,
            "elapsed_minutes": elapsed_minutes,
            "next_check_in": 10
        }


@celery_app.task(base=CallbackTask, bind=True)
def parse_logs_and_post_status(self, job_id: str, repo_url: str, commit_sha: str, job_status: str) -> Dict[str, Any]:
    """Parse job logs and post final status to GitHub."""
    logger.info(f"Parsing logs and posting status for job: {job_id}")

    # TODO: Implement log parsing and GitHub status posting

    # Simulate log parsing
    import time
    time.sleep(1)

    # Map job status to GitHub status (using GitHub API status strings)
    github_status = "success" if job_status == "completed" else "failure"

    return {
        "status": TaskStatus.COMPLETED,
        "job_id": job_id,
        "repo_url": repo_url,
        "commit_sha": commit_sha,
        "final_job_status": job_status,
        "github_status": github_status,
        "posted_to_github": True
    }
