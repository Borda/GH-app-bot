import json
import logging
import shutil
import traceback
from datetime import datetime
from enum import Enum
from functools import lru_cache
from pathlib import Path

import redis
from aiohttp import ClientSession
from gidgethub.aiohttp import GitHubAPI
from gidgethub.apps import get_installation_access_token
from lightning_sdk import Job, Status, Teamspace
from lightning_sdk.lightning_cloud.env import LIGHTNING_CLOUD_URL

from _bots_commons.configs import ConfigFile, ConfigRun, ConfigWorkflow, GitHubRunConclusion, GitHubRunStatus
from _bots_commons.downloads import download_repo_and_extract
from _bots_commons.lit_job import finalize_job, job_run
from _bots_commons.utils import (
    _load_validate_required_env_vars,
    create_jwt_token,
    exceeded_timeout,
    extract_repo_details,
    gh_patch_with_retry,
    gh_post_with_retry,
    wrap_long_text,
)
from bot_redis_workers import REDIS_QUEUE

MAX_OUTPUT_LENGTH = 65525  # GitHub API limit for check-run output.text
LIT_JOB_QUEUE_TIMEOUT = 60 * 60  # 1 hour
LIT_STATUS_RUNNING = {Status.Running, Status.Stopping}
LIT_STATUS_FINISHED = {Status.Completed, Status.Stopped, Status.Failed}
LIT_STATUS_RUNNING_OR_FINISHED = LIT_STATUS_RUNNING | LIT_STATUS_FINISHED
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
        logging.warning(log_prefix + "Failed to extract `.lightning` folder from repo.")
    return config_files, config_dir, config_error


def push_to_redis(redis_client: redis.Redis, task: dict):
    redis_client.rpush(REDIS_QUEUE, json.dumps(task))


async def _post_gh_run_status_missing_configs(gh, gh_url, head_sha: str, text: str) -> None:
    await gh_post_with_retry(
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
    await gh_post_with_retry(
        gh=gh,
        url=gh_url,
        data={
            "name": f"{cfg_file.name} / {config.name} [{event_type}]",
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": "skipped",
            "started_at": datetime.utcnow().isoformat() + "Z",
            "output": {
                "title": "Skipped",
                "summary": f"Configuration `{cfg_file.name}` is not triggered"
                f" by the event `{event_type}` on branch `{branch_ref}` (with `{config.trigger}`).",
            },
        },
    )


async def _post_gh_run_status_create_check(gh, gh_url, head_sha: str, run_name: str, link_lit_jobs: str) -> str:
    check = await gh_post_with_retry(
        gh=gh,
        url=gh_url,
        data={"name": run_name, "head_sha": head_sha, "status": "queued", "details_url": link_lit_jobs},
    )
    return check["id"]


async def _post_gh_run_status_update_check(
    gh,
    gh_url: str,
    title: str,
    run_status: GitHubRunStatus,
    run_conclusion: GitHubRunConclusion | None = None,
    started_at: str = "",
    url_job: str = "",
    summary: str = "",
    text: str = "",
) -> None:
    if not started_at:
        started_at = datetime.utcnow().isoformat() + "Z"
    patch_data = {
        "status": run_status.value,
        "started_at": started_at,
        "output": {"summary": summary},
        "details_url": url_job,
    }
    if title:
        patch_data["output"].update({"title": title})
    if text:
        patch_data["output"].update({"text": text})
    if run_conclusion:
        patch_data.update({"conclusion": run_conclusion.value})
    await gh_patch_with_retry(gh=gh, url=gh_url, data=patch_data)


async def process_task_with_session(task: dict, redis_client: redis.Redis) -> None:
    """Wrapper that manages the aiohttp session for the entire task processing."""
    async with ClientSession() as session:
        await _process_task_inner(task, redis_client, session)


async def process_job_pending(gh, task: dict, lit_job: Job) -> dict | None:
    config_run = ConfigRun(**task["run_config"])
    job_name = task["job_name"]
    url_check_id = f"/repos/{config_run.repository_owner}/{config_run.repository_name}/check-runs/{task['check_id']}"
    if exceeded_timeout(task["job_start_time"], timeout_seconds=LIT_JOB_QUEUE_TIMEOUT):
        lit_job.stop()
        await _post_gh_run_status_update_check(
            gh=gh,
            gh_url=url_check_id,
            title="Job has timed out",
            run_status=GitHubRunStatus.COMPLETED,
            run_conclusion=GitHubRunConclusion.CANCELLED,
            started_at=task["job_start_time"],
            url_job=lit_job.link + "&job_detail_tab=logs",
            summary="Wait for machine availability too long",
            text=f"Job `{job_name}` didn't start within the provided ({LIT_JOB_QUEUE_TIMEOUT}) timeout.",
        )
        return None

    await _post_gh_run_status_update_check(
        gh=gh,
        gh_url=url_check_id,
        title="Job is pending",
        run_status=GitHubRunStatus.QUEUED,
        started_at=task["job_start_time"],
        url_job=lit_job.link + "&job_detail_tab=logs",
        summary=f"Job `{job_name}` is waiting for machine availability",
    )
    return task


async def process_job_running(gh, task, lit_job: Job) -> dict | None:
    config_run = ConfigRun(**task["run_config"])
    job_name = task["job_name"]
    url_check_id = f"/repos/{config_run.repository_owner}/{config_run.repository_name}/check-runs/{task['check_id']}"
    if exceeded_timeout(task["job_start_time"], timeout_seconds=config_run.timeout_minutes * 60):
        lit_job.stop()
        await _post_gh_run_status_update_check(
            gh=gh,
            gh_url=url_check_id,
            title="Job has timed out",
            run_status=GitHubRunStatus.COMPLETED,
            run_conclusion=GitHubRunConclusion.CANCELLED,
            started_at=task["job_start_time"],
            url_job=lit_job.link + "&job_detail_tab=logs",
            summary="Job exceeded timeout limit.",
            text=f"Job `{job_name}` didn't finish within the provided ({config_run.timeout_minutes}) minutes.",
        )
        return None

    # case when it switched from pending to running, so last time it was not running
    if Status(task["job_status"]) not in LIT_STATUS_RUNNING_OR_FINISHED:
        task.update({"job_status": lit_job.status.value, "job_start_time": datetime.utcnow().isoformat() + "Z"})

    await _post_gh_run_status_update_check(
        gh=gh,
        gh_url=url_check_id,
        title="Job is running",
        run_status=GitHubRunStatus.IN_PROGRESS,
        started_at=task["job_start_time"],
        url_job=lit_job.link + "&job_detail_tab=logs",
        summary=f"Job `{job_name}` is running on Lightning Cloud",
    )
    return task


async def _process_task_inner(task: dict, redis_client: redis.Redis, session) -> None:
    task_phase = TaskPhase(task["phase"])
    event_type = task["event_type"]
    payload = task["payload"]
    delivery_id = task["delivery_id"]
    repo_owner, repo_name, head_sha, branch_ref = extract_repo_details(event_type=event_type, payload=payload)
    log_prefix = f"{repo_owner}/{repo_name}::{head_sha[:7]}::{task['event_type']}::\t"

    github_app_id, app_private_key, webhook_secret = _load_validate_required_env_vars()
    jwt_token = create_jwt_token(github_app_id=github_app_id, app_private_key=app_private_key)
    # Exchange JWT for installation token
    app_gh = GitHubAPI(session, "bot_redis_workers", oauth_token=jwt_token)
    installation_id = payload["installation"]["id"]
    token_resp = await get_installation_access_token(
        app_gh, installation_id=installation_id, app_id=str(github_app_id), private_key=app_private_key
    )
    inst_token = token_resp["token"]
    gh = GitHubAPI(session, "bot_async_tasks", oauth_token=inst_token)
    gh_url_runs = f"/repos/{repo_owner}/{repo_name}/check-runs"
    post_kwargs = {"gh": gh, "gh_url": gh_url_runs, "head_sha": head_sha}

    if task_phase == TaskPhase.NEW_EVENT:
        config_files, config_dir, config_error = await generate_run_configs(
            event_type=event_type, delivery_id=delivery_id, payload=payload, auth_token=inst_token
        )
        if not config_files:
            logging.warning(log_prefix + f"No valid configs found in {config_dir}")
            text_error = (
                f"```console\n{config_error!s}\n```" if config_error else "No specific error details available."
            )
            await _post_gh_run_status_missing_configs(text=text_error, **post_kwargs)
            return
        for cfg_file in config_files:
            config = ConfigWorkflow(cfg_file.body, file_name=cfg_file.name)
            config.append_repo_details(
                repo_owner=repo_owner, repo_name=repo_name, head_sha=head_sha, branch_ref=branch_ref
            )
            if not config.is_triggered_by_event(event=event_type, branch=branch_ref):
                if event_type in config.trigger:
                    # there is a trigger for this event, but it is not matched
                    await _post_gh_run_status_not_triggered(
                        event_type=event_type, branch_ref=branch_ref, cfg_file=cfg_file, config=config, **post_kwargs
                    )
                # skip this config if it is not triggered by the event
                logging.info(
                    log_prefix + f"Skipping config {cfg_file.name} for event '{event_type}' on branch '{branch_ref}'"
                    f" because it is not triggered by this event with `{config.trigger}`."
                )
                continue
            # Generate configs
            count = 0
            for config_run in config.generate_runs():
                task.update({"phase": TaskPhase.START_JOB.value, "run_config": config_run.to_dict()})
                push_to_redis(redis_client, task)
                count += 1
            logging.info(log_prefix + f"Enqueued {count} jobs for config '{cfg_file.name}'")
        return

    if task_phase == TaskPhase.START_JOB:
        config_run = ConfigRun(**task["run_config"])
        debug_mode = config_run.mode == "debug"
        logging.info(log_prefix + f"Starting litJob for config '{config_run.name}'")
        run_params = [p or "n/a" for p in config_run.params.values()]
        run_name = f"{config_run.file_name} / {config_run.name} ({', '.join(run_params)})"
        # Create a check run
        link_lightning_jobs = f"{LIGHTNING_CLOUD_URL}/{this_teamspace().owner.name}/{this_teamspace().name}/jobs/"
        check_id = await _post_gh_run_status_create_check(
            run_name=run_name, link_lit_jobs=link_lightning_jobs, **post_kwargs
        )
        url_check_id = f"/repos/{repo_owner}/{repo_name}/check-runs/{check_id}"
        job_name = (
            f"ci-run_{repo_owner}-{repo_name}-{head_sha[:7]}"
            f"-event-{delivery_id.split('-')[0]}-{run_name.replace(' ', '_')}"
        )
        try:  # Start litJob
            job, logs_separator, exit_separator = await job_run(
                cfg_file_name=config_run.file_name, config=config_run, gh_token=inst_token, job_name=job_name
            )
        except Exception as ex:
            logging.error(f"Failed to run job `{job_name}`: {ex!s}\n{traceback.format_exc()}")
            text = f"```console\n{ex!s}\n```" if debug_mode else "No specific error details available."
            await _post_gh_run_status_update_check(
                gh=gh,
                gh_url=url_check_id,
                title="Failed to run litJob",
                run_status=GitHubRunStatus.COMPLETED,
                run_conclusion=GitHubRunConclusion.FAILURE,
                url_job=link_lightning_jobs,
                summary=f"Job `{job_name}` failed.",
                text=text,
            )
            return

        task.update({
            "phase": TaskPhase.WAIT_JOB.value,
            "check_id": check_id,
            "run_name": run_name,
            "job_reference": {"name": job.name, "teamspace": job.teamspace.name, "org": job.teamspace.owner.name},
            "job_name": job_name,
            "job_status": job.status.value,
            "job_start_time": datetime.utcnow().isoformat() + "Z",
            "job_logs_separator": logs_separator,
            "job_exit_separator": exit_separator,
        })
        push_to_redis(redis_client, task)
        logging.info(log_prefix + f"Enqueued litJob for config '{config_run.name}'")
        return

    if task_phase == TaskPhase.WAIT_JOB:
        job_name = task["job_name"]
        job_ref = task["job_reference"]
        lit_job = Job(name=job_ref["name"], teamspace=job_ref["teamspace"], org=job_ref["org"])
        if lit_job.status not in LIT_STATUS_RUNNING_OR_FINISHED:
            task = await process_job_pending(gh=gh, task=task, lit_job=lit_job)
            if task is None:
                logging.warning(log_prefix + f"Job {job_name} didn't start within the provided timeout.")
                return
            push_to_redis(redis_client, task)
            logging.debug(log_prefix + f"Job {job_name} still pending, re-enqueued")
            return

        if lit_job.status in LIT_STATUS_RUNNING:
            task = await process_job_running(gh=gh, task=task, lit_job=lit_job)
            if task is None:
                logging.warning(log_prefix + f"Job {job_name} didn't finish within the provided timeout.")
                return
            push_to_redis(redis_client, task)
            logging.debug(log_prefix + f"Job {job_name} status changed to {lit_job.status.value}, re-enqueued")
            return

        assert lit_job.status in LIT_STATUS_FINISHED, f"'{lit_job.status}' is not a finished as `{LIT_STATUS_FINISHED}`"
        task.update({"phase": TaskPhase.RESULTS.value})
        # Update status to QUEUED, so it will be processed in the next iteration
        push_to_redis(redis_client, task)
        logging.info(log_prefix + f"Enqueued results for job '{job_name}'")
        return

    if task_phase == TaskPhase.RESULTS:
        config_run = ConfigRun(**task["run_config"])
        debug_mode = config_run.mode == "debug"
        job_name = task["job_name"]
        job_ref = task["job_reference"]
        lit_job = Job(name=job_ref["name"], teamspace=job_ref["teamspace"], org=job_ref["org"])
        url_check_id = f"/repos/{repo_owner}/{repo_name}/check-runs/{task['check_id']}"

        job_status, exit_code, results = finalize_job(
            lit_job, logs_hash=task["job_logs_separator"], exit_hash=task["job_exit_separator"], debug=debug_mode
        )
        if exit_code is None:  # if the job didn't return an exit code
            run_conclusion = GitHubRunConclusion.NEUTRAL
        elif exit_code == 0:  # if the job finished successfully
            run_conclusion = GitHubRunConclusion.SUCCESS
        else:  # if the job failed
            run_conclusion = GitHubRunConclusion.FAILURE
        results = wrap_long_text(results, text_length=MAX_OUTPUT_LENGTH - 20)  # wrap the results to fit in the output
        await _post_gh_run_status_update_check(
            gh=gh,
            gh_url=url_check_id,
            title="Job finished",
            run_status=GitHubRunStatus.COMPLETED,
            run_conclusion=run_conclusion,
            started_at=task["job_start_time"],
            url_job=lit_job.link + "&job_detail_tab=logs",
            summary=f"Job '{job_name}' finished as `{job_status}` with exit code `{exit_code}`.",
            text=f"```console\n{results or 'No results available'}\n```",
        )
        logging.info(log_prefix + f"Job {job_name} finished as `{job_status}` with exit code `{exit_code}`.")
        return

    logging.error(f"Unknown task type: {task_phase}")
