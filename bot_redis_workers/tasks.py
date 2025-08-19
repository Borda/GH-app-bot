import json
import logging
from enum import Enum

import redis

from bot_redis_workers import REDIS_QUEUE


class TaskPhase(Enum):
    """Life-cycle phases for tasks."""

    NEW_EVENT = "new_event"
    START_JOB = "start_job"
    WAIT_JOB = "wait_job"
    RESULTS = "results"


# Placeholder functions - replace with your actual litJob (Lightning Job) logic
def start_lit_job(config, repo_path):
    # Start a Lightning job with the config
    # e.g., job = LightningWork(... config ...)
    # Return a job_id
    print(f"Starting litJob with config {config} in repo {repo_path}")
    return "dummy_job_id"  # Replace with actual


def check_lit_job_status(job_id):
    # Check status: 'running', 'finished', 'failed'
    print(f"Checking status of litJob {job_id}")
    return "finished"  # Placeholder


def get_lit_job_logs(job_id):
    # Get logs
    print(f"Getting logs for litJob {job_id}")
    return "dummy logs"  # Placeholder


def summarize_logs(logs):
    # Summarize
    print(f"Summarizing logs: {logs}")
    return "Summary: All good!"  # Placeholder


async def post_to_github(gh, repo_full_name, pr_number, comment):
    # Post comment to PR
    print(f"Posting comment to PR #{pr_number} in repo {repo_full_name}: {comment}")
    await gh.post(f"/repos/{repo_full_name}/issues/{pr_number}/comments", data={"body": comment})


# Generate run configs - placeholder
def generate_run_configs(repo_path):
    # e.g., read .litci.yaml from repo_path, generate list of dicts (envs, etc.)
    return [{"env": "python3.8"}, {"env": "python3.10"}]  # Placeholder


def push_to_redis(redis_client: redis.Redis, task: dict):
    redis_client.rpush(REDIS_QUEUE, json.dumps(task))


def process_task(task: dict, redis_client: redis.Redis):
    task_phase = TaskPhase(task["phase"])

    if task_phase == TaskPhase.NEW_EVENT:
        # Pull repo
        # payload = task["payload"]
        # repo_url = payload["repository"]["clone_url"]
        repo_path = "/tmp/repo_"  # Temp dir
        # git.Repo.clone_from(repo_url, repo_path)  # Or pull if exists

        # Generate configs
        configs = generate_run_configs(repo_path)
        for config in configs:
            push_to_redis(
                redis_client,
                {
                    "phase": TaskPhase.START_JOB.value,
                    "config": config,
                    "repo_path": repo_path,
                },
            )
        print(f"Enqueued {len(configs)} start_job tasks")

    elif task_phase == TaskPhase.START_JOB:
        # Start litJob
        job_id = start_lit_job(task["config"], task["repo_path"])
        push_to_redis(
            redis_client,
            {
                "phase": TaskPhase.WAIT_JOB.value,
                "job_id": job_id,
                "status": "running",
            },
        )
        print(f"Started job {job_id}, enqueued wait_job")

    elif task_phase == TaskPhase.WAIT_JOB:
        # Check status
        status = check_lit_job_status(task["job_id"])
        if status == "running":
            # Put back to queue
            push_to_redis(redis_client, task)
            print(f"Job {task['job_id']} still running, re-enqueued")
        elif status == "finished":
            push_to_redis(
                redis_client,
                {
                    "phase": TaskPhase.RESULTS.value,
                    "job_id": task["job_id"],
                },
            )
            print(f"Job {task['job_id']} finished, enqueued process_results")
        # todo: Handle failed, etc.

    elif task_phase == TaskPhase.RESULTS:
        # Process logs
        logs = get_lit_job_logs(task["job_id"])
        summary = summarize_logs(logs)

        # Report to GH (use sync wrapper for async if needed, but here assume sync)
        # For real, you might need to create a sync GitHubAPI
        # Placeholder: print summary
        print(f"PR: {summary}")

        # todo
        # Actual: Use gidgethub sync or wrap
        # from gidgethub import GitHubAPI as SyncGH
        # gh = SyncGH(...)  # Get token similarly
        # gh.post(...)

    else:
        logging.warn(f"Unknown task type: {task_phase}")
