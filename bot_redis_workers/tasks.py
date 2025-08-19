import json
import logging
import shutil
from datetime import datetime
from enum import Enum
from functools import lru_cache
from pathlib import Path

import redis
from aiohttp import ClientSession
from gidgethub.aiohttp import GitHubAPI
from gidgethub.apps import get_installation_access_token
from lightning_sdk import Teamspace

from bot_async_tasks.downloads import download_repo_and_extract
from bot_commons.configs import ConfigFile, ConfigWorkflow
from bot_commons.utils import _load_validate_required_env_vars, create_jwt_token, extract_repo_details, post_with_retry
from bot_redis_workers import REDIS_QUEUE

LOCAL_ROOT_DIR = Path(__file__).parent
LOCAL_TEMP_DIR = LOCAL_ROOT_DIR / ".temp"


class TaskPhase(Enum):
    """Life-cycle phases for tasks."""

    NEW_EVENT = "new_event"
    START_JOB = "start_job"
    WAIT_JOB = "wait_job"
    RESULTS = "results"


@lru_cache
def this_teamspace() -> Teamspace:
    """Get the current Teamspace instance."""
    return Teamspace()


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


# Generate run configs
async def generate_run_configs(
    event_type: str, delivery_id: str, payload: dict, auth_token: str
) -> tuple[list[ConfigFile], Path | None, Exception | None]:
    repo_owner, repo_name, head_sha, branch_ref = extract_repo_details(event_type, payload)
    config_files, config_dir, config_error = [], None, None
    log_prefix = f"{repo_owner}/{repo_name}::{head_sha[:7]}::{event_type}::\t"

    # 1) Download the repository at the specified ref
    repo_dir = await download_repo_and_extract(
        repo_owner=repo_owner,
        repo_name=repo_name,
        git_ref=head_sha,
        auth_token=auth_token,
        folder_path=LOCAL_TEMP_DIR,
        # extract only the `.lightning` subfolder
        subfolder=".lightning",  # extract only `.lightning` subfolder
        suffix=f"event-{delivery_id}",
    )

    # 2) Read the config file
    if repo_dir and repo_dir.is_dir():
        logging.info(log_prefix + f"Downloaded repo to {repo_dir}")
        repo_dir = Path(repo_dir).resolve()
        config_dir = repo_dir / ".lightning" / "workflows"
        try:
            config_files = ConfigFile.load_from_folder(config_dir)
        except Exception as ex:
            config_error = ex
        finally:
            logging.info(log_prefix + f"Cleaning up the repo directory: {repo_dir}")
            shutil.rmtree(repo_dir, ignore_errors=True)
    else:
        logging.warn(log_prefix + "Failed to extract `.lightning` folder from repo.")
    return config_files, config_dir, config_error


def push_to_redis(redis_client: redis.Redis, task: dict):
    redis_client.rpush(REDIS_QUEUE, json.dumps(task))


async def _post_gh_run_status_missing_configs(gh, gh_url, head_sha: str, text: str) -> None:
    await post_with_retry(
        gh=gh,
        url=gh_url,
        data={
            "name": "Lit bot",
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": "skipped",
            "started_at": datetime.utcnow().isoformat() + "Z",
            "output": {
                "title": "No Configs Found",
                "summary": "No valid configuration files found in `.lightning/workflows` folder.",
                "text": text,
            },
        },
    )


async def _post_gh_run_status_not_triggered(
    gh, gh_url, head_sha: str, event_type: str, branch_ref: str, cfg_file: ConfigFile, config: ConfigWorkflow
) -> None:
    await post_with_retry(
        gh=gh,
        url=gh_url,
        data={
            "name": f"{cfg_file.name} / {config.name} [{event_type}]",
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": "skipped",
            "started_at": datetime.datetime.utcnow().isoformat() + "Z",
            "output": {
                "title": "Skipped",
                "summary": f"Configuration `{cfg_file.name}` is not triggered"
                f" by the event `{event_type}` on branch `{branch_ref}` (with `{config.trigger}`).",
            },
        },
    )


async def process_task(task: dict, redis_client: redis.Redis) -> None:
    task_phase = TaskPhase(task["phase"])
    event_type = task["event_type"]
    repo_owner, repo_name, head_sha, branch_ref = extract_repo_details(event_type=event_type, payload=task["payload"])
    log_prefix = f"{repo_owner}/{repo_name}::{head_sha[:7]}::{task['event_type']}::\t"

    github_app_id, app_private_key, webhook_secret = _load_validate_required_env_vars()
    jwt_token = create_jwt_token(github_app_id=github_app_id, app_private_key=app_private_key)
    async with ClientSession() as session:
        # Exchange JWT for installation token
        app_gh = GitHubAPI(session, "bot_redis_workers", oauth_token=jwt_token)
        installation_id = task["payload"]["installation"]["id"]
        token_resp = await get_installation_access_token(
            app_gh, installation_id=installation_id, app_id=str(github_app_id), private_key=app_private_key
        )
    inst_token = token_resp["token"]
    gh = GitHubAPI(session, "bot_async_tasks", oauth_token=inst_token)
    gh_url_runs = f"/repos/{repo_owner}/{repo_name}/check-runs"

    if task_phase == TaskPhase.NEW_EVENT:
        config_files, config_dir, config_error = generate_run_configs(
            event_type=event_type, delivery_id=task["delivery_id"], payload=task["payload"], auth_token=inst_token
        )
        if not config_files:
            logging.warn(log_prefix + f"No valid configs found in {config_dir}")
            text_error = (
                f"```console\n{config_error!s}\n```" if config_error else "No specific error details available."
            )
            await _post_gh_run_status_missing_configs(gh, gh_url=gh_url_runs, head_sha=head_sha, text=text_error)
            return
        for cfg_file in config_files:
            config = ConfigWorkflow(cfg_file.body)
            config.append_repo_details(
                repo_owner=repo_owner, repo_name=repo_name, head_sha=head_sha, branch_ref=branch_ref
            )
            if not config.is_triggered_by_event(event=event_type, branch=branch_ref):
                if event_type in config.trigger:
                    # there is a trigger for this event, but it is not matched
                    await _post_gh_run_status_not_triggered(
                        gh=gh,
                        gh_url=gh_url_runs,
                        head_sha=head_sha,
                        event_type=event_type,
                        branch_ref=branch_ref,
                        cfg_file=cfg_file,
                        config=config,
                    )
                # skip this config if it is not triggered by the event
                logging.info(
                    log_prefix + f"Skipping config {cfg_file.name} for event '{event_type}' on branch '{branch_ref}'"
                    f" because it is not triggered by this event with `{config.trigger}`."
                )
                continue
            # Generate configs
            counter = 0
            for config_run in config.generate_runs():
                task.update({"phase": TaskPhase.START_JOB.value, "config": config_run})
                push_to_redis(redis_client, task)
                counter += 1
            logging.info(log_prefix + f"Enqueued {len(counter)} jobs for config '{cfg_file.name}'")

    elif task_phase == TaskPhase.START_JOB:
        # Start litJob
        job_id = start_lit_job(task["config"], task["repo_path"])
        task.update({"phase": TaskPhase.WAIT_JOB.value, "job_id": job_id, "status": "running"})
        push_to_redis(redis_client, task)
        print(f"Started job {job_id}, enqueued wait_job")

    elif task_phase == TaskPhase.WAIT_JOB:
        # Check status
        status = check_lit_job_status(task["job_id"])
        if status == "running":
            # Put back to queue
            push_to_redis(redis_client, task)
            print(f"Job {task['job_id']} still running, re-enqueued")
        elif status == "finished":
            task.update({"phase": TaskPhase.RESULTS.value})
            push_to_redis(redis_client, task)
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
        logging.warning(f"Unknown task type: {task_phase}")
