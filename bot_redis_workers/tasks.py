import json
import logging

import redis

from bot_redis_workers.types import TaskType


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


def process_task(task: dict, redis_client: redis.Redis):
    task_type = task["type"]

    if task_type == TaskType.NEW_EVENT:
        # Pull repo
        # payload = task["payload"]
        # repo_url = payload["repository"]["clone_url"]
        repo_path = f"/tmp/repo_{task['pr_number']}"  # Temp dir
        # git.Repo.clone_from(repo_url, repo_path)  # Or pull if exists

        # Generate configs
        configs = generate_run_configs(repo_path)
        for config in configs:
            new_task = {
                "type": "start_job",
                "config": config,  # todo: Placeholder
                "repo_path": repo_path,
            }
            redis_client.rpush("bot_queue", json.dumps(new_task))
        print(f"Enqueued {len(configs)} start_job tasks")

    elif task_type == TaskType.START_JOB:
        # Start litJob
        job_id = start_lit_job(task["config"], task["repo_path"])
        new_task = {
            "type": "wait_job",
            "job_id": job_id,
            "status": "running",
        }
        redis_client.rpush("bot_queue", json.dumps(new_task))
        print(f"Started job {job_id}, enqueued wait_job")

    elif task_type == TaskType.WAIT_JOB:
        # Check status
        status = check_lit_job_status(task["job_id"])
        if status == "running":
            # Put back to queue
            redis_client.rpush("bot_queue", json.dumps(task))
            print(f"Job {task['job_id']} still running, re-enqueued")
        elif status == "finished":
            new_task = {
                "type": "process_results",
                "job_id": task["job_id"],
            }
            redis_client.rpush("bot_queue", json.dumps(new_task))
            print(f"Job {task['job_id']} finished, enqueued process_results")
        # Handle failed, etc.

    elif task_type == TaskType.RESULTS:
        # Process logs
        logs = get_lit_job_logs(task["job_id"])
        summary = summarize_logs(logs)

        # Report to GH (use sync wrapper for async if needed, but here assume sync)
        # For real, you might need to create a sync GitHubAPI
        # Placeholder: print summary
        print(f"PR: {summary}")

        # Actual: Use gidgethub sync or wrap
        # from gidgethub import GitHubAPI as SyncGH
        # gh = SyncGH(...)  # Get token similarly
        # gh.post(...)

    else:
        logging.warn(f"Unknown task type: {task_type}")
