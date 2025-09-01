import json
import logging
import shutil
import traceback
from datetime import datetime
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

import redis
from aiohttp import ClientSession
from gidgethub.aiohttp import GitHubAPI
from lightning_sdk import Job, Status, Teamspace
from lightning_sdk.lightning_cloud.env import LIGHTNING_CLOUD_URL

from _bots_commons import LOCAL_TEMP_DIR, MAX_OUTPUT_LENGTH
from _bots_commons.configs import ConfigFile, ConfigRun, ConfigWorkflow, GitHubRunConclusion, GitHubRunStatus
from _bots_commons.downloads import download_repo_and_extract
from _bots_commons.gh_posts import (
    _get_gh_app_token,
    post_gh_run_status_create_check,
    post_gh_run_status_missing_configs,
    post_gh_run_status_not_triggered,
    post_gh_run_status_update_check,
)
from _bots_commons.lit_job import (
    LIT_JOB_QUEUE_TIMEOUT,
    LIT_STATUS_FINISHED,
    LIT_STATUS_RUNNING,
    LIT_STATUS_RUNNING_OR_FINISHED,
    _restore_lit_job_from_task,
    finalize_job,
    job_run,
)
from _bots_commons.utils import (
    exceeded_timeout,
    extract_repo_details,
    wrap_long_text,
)
from bot_redis_workers import REDIS_QUEUE


class TaskPhase(Enum):
    """Life-cycle phases for tasks."""

    NEW_EVENT = "new_event"
    START_JOB = "start_job"
    WAIT_JOB = "wait_job"
    FAILURE = "failure"
    RESULTS = "results"


@lru_cache
def this_teamspace() -> Teamspace:
    """Get the current Teamspace instance."""
    return Teamspace()


# Generate run configs
async def generate_run_configs(
    event_type: str, delivery_id: str, payload: dict[str, Any], auth_token: str, log_prefix: str
) -> tuple[list[ConfigFile], Path | None, Exception | None]:
    """Generate run configs for the given event.

    Args:
        event_type: The type of the event.
        delivery_id: The delivery ID of the event.
        payload: The payload of the event.
        auth_token: The authentication token for the GitHub API.
        log_prefix: The prefix to use for logging.

    Returns:
        A tuple containing the list of config files, the directory containing the config files, and the error.
    """
    repo_owner, repo_name, head_sha, branch_ref = extract_repo_details(event_type, payload)
    config_files, config_dir, config_error = [], None, None

    # 1) Download the repository at the specified ref
    repo_dir = await download_repo_and_extract(
        repo_owner=repo_owner,
        repo_name=repo_name,
        git_ref=head_sha,
        auth_token=auth_token,
        folder_path=LOCAL_TEMP_DIR,
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


def push_to_redis(redis_client: redis.Redis, task: dict[str, Any]) -> None:
    """Push task to Redis queue.

    Args:
        redis_client: The Redis client instance.
        task: The task to push.
    """
    # todo: replace with Celery so you can set some time for staying in the queue
    #  and so limit nb requests for example when waiting
    redis_client.rpush(REDIS_QUEUE, json.dumps(task))


async def process_task_with_session(task: dict[str, Any], redis_client: redis.Redis) -> None:
    """Wrapper that manages an aiohttp session for end-to-end task processing.

    Args:
        task: A serialized task dictionary pulled from Redis.
        redis_client: Redis client used to re-enqueue or push follow-up tasks.
    """
    async with ClientSession() as session:
        return await _process_task_inner(task, redis_client, session)


async def process_job_pending(gh: GitHubAPI, task: dict[str, Any], lit_job: Job) -> dict[str, Any]:
    """Check the status of a pending job and validate waiting time.

    Args:
        gh: Authenticated GitHub API client.
        task: Task dict carrying job context.
        lit_job: Lightning Job instance reconstructed from the task.

    Returns:
        The possibly-updated task dict (phase may switch to FAILURE).
    """
    config_run = ConfigRun(**task["run_config"])
    job_name = task["job_name"]
    url_check_id = f"/repos/{config_run.repository_owner}/{config_run.repository_name}/check-runs/{task['check_id']}"
    if exceeded_timeout(task["job_start_time"], timeout_seconds=LIT_JOB_QUEUE_TIMEOUT):
        lit_job.stop()
        await post_gh_run_status_update_check(
            gh=gh,
            gh_url=url_check_id,
            title="Job has timed out",
            run_status=GitHubRunStatus.COMPLETED,
            run_conclusion=GitHubRunConclusion.CANCELLED,
            started_at=task["job_start_time"],
            url_job=lit_job.link + "&job_detail_tab=logs",
            summary="Wait for machine availability too long",
            text=f"Job **{job_name}** didn't start within the provided ({LIT_JOB_QUEUE_TIMEOUT}) timeout.",
        )
        task.update({"phase": TaskPhase.FAILURE.value})
        return task

    await post_gh_run_status_update_check(
        gh=gh,
        gh_url=url_check_id,
        title="Job is pending",
        run_status=GitHubRunStatus.QUEUED,
        started_at=task["job_start_time"],
        url_job=lit_job.link + "&job_detail_tab=logs",
        summary=f"Job **{job_name}** is waiting for machine availability",
    )
    return task


async def process_job_running(gh: GitHubAPI, task: dict[str, Any], lit_job: Job) -> dict[str, Any]:
    """Check the status of a running job and validate running time.

    Args:
        gh: Authenticated GitHub API client.
        task: Task dict carrying job context.
        lit_job: Lightning Job instance reconstructed from the task.

    Returns:
        The possibly-updated task dict (phase may switch to FAILURE).
    """
    config_run = ConfigRun(**task["run_config"])
    job_name = task["job_name"]
    url_check_id = f"/repos/{config_run.repository_owner}/{config_run.repository_name}/check-runs/{task['check_id']}"
    if exceeded_timeout(task["job_start_time"], timeout_seconds=config_run.timeout_minutes * 60):
        lit_job.stop()
        await post_gh_run_status_update_check(
            gh=gh,
            gh_url=url_check_id,
            title="Job has timed out",
            run_status=GitHubRunStatus.COMPLETED,
            run_conclusion=GitHubRunConclusion.CANCELLED,
            started_at=task["job_start_time"],
            url_job=lit_job.link + "&job_detail_tab=logs",
            summary="Job exceeded timeout limit.",
            text=f"Job **{job_name}** didn't finish within the provided ({config_run.timeout_minutes}) minutes.",
        )
        task.update({"phase": TaskPhase.FAILURE.value})
        return task

    # case when it switched from pending to running, so last time it was not running
    if Status(task["job_status"]) not in LIT_STATUS_RUNNING_OR_FINISHED:
        task.update({"job_status": lit_job.status.value, "job_start_time": datetime.utcnow().isoformat() + "Z"})

    await post_gh_run_status_update_check(
        gh=gh,
        gh_url=url_check_id,
        title="Job is running",
        run_status=GitHubRunStatus.IN_PROGRESS,
        started_at=task["job_start_time"],
        url_job=lit_job.link + "&job_detail_tab=logs",
        summary=f"Job **{job_name}** is running on Lightning Cloud",
    )
    return task


async def _process_task_inner(task: dict[str, Any], redis_client: redis.Redis, session: ClientSession) -> None:
    """This is the main function that processes a task with phases.

    Key Multipliers:
    ================
    1. Event → Config Files (1:N relationship)
       - Single event can trigger multiple .yaml/.yml config files
       - Each config file in .lightning/workflows/ folder is processed
       - Configs are filtered by event type and branch triggers

    2. Config File → Job Runs (1:M relationship)
       - Each config can have multiple workflow definitions
       - Each workflow can generate multiple runs based on parameters
       - Matrix builds, parameter combinations, etc.

    3. Job Run → Lightning AI Job (1:1 relationship)
       - Each job run creates exactly one Lightning AI job
       - Jobs are queued independently in Redis
       - Each job has its own lifecycle (START_JOB → WAIT_JOB → RESULTS)

    Example Scenario:
    ================
    GitHub Push Event → 3 Config Files → 8 Total Jobs
    ├─ ci.yml (2 jobs: Python 3.9, Python 3.11)
    ├─ tests.yml (4 jobs: unit, integration, e2e, performance)
    └─ deploy.yml (2 jobs: staging, production)

    Redis Task Queue Flow:
    =====================
    Each job becomes an independent task in Redis with phases:
    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
    │ NEW_EVENT   │───▶│ START_JOB   │───▶│ WAIT_JOB    │───▶│ RESULTS     │
    │ (1 task)    │    │ (N tasks)   │    │ (Monitor)   │    │ (Finalize)  │
    └─────────────┘    └─────────────┘    └──────┬──────┘    └─────────────┘
                                                 │
                                                 ▼
                                          ┌─────────────┐
                                          │ FAILURE     │
                                          │ (Handle     │
                                          │  timeouts)  │
                                          └─────────────┘
    """
    task_phase = TaskPhase(task["phase"])
    event_type = task["event_type"]
    payload = task["payload"]
    delivery_id = task["delivery_id"]
    repo_owner, repo_name, head_sha, branch_ref = extract_repo_details(event_type=event_type, payload=payload)
    log_prefix = f"{repo_owner}/{repo_name}::{head_sha[:7]}::{task['event_type']}::\t"

    inst_token = await _get_gh_app_token(session, payload=payload)
    gh = GitHubAPI(session, "bot_async_tasks", oauth_token=inst_token)
    gh_url_runs = f"/repos/{repo_owner}/{repo_name}/check-runs"
    post_kwargs = {"gh": gh, "gh_url": gh_url_runs, "head_sha": head_sha}

    # =====================================================
    # EVENT PROCESSING
    # This stage handles incoming GitHub webhook events and extracts configuration files from repositories.
    # =====================================================
    if task_phase == TaskPhase.NEW_EVENT:
        config_files, config_dir, config_error = await generate_run_configs(
            event_type=event_type,
            delivery_id=delivery_id,
            payload=payload,
            auth_token=inst_token,
            log_prefix=log_prefix,
        )
        if not config_files:
            logging.warning(log_prefix + f"No valid configs found in {config_dir}")
            text_error = (
                f"```console\n{config_error!s}\n```" if config_error else "No specific error details available."
            )
            await post_gh_run_status_missing_configs(text=text_error, **post_kwargs)
            return
        for cfg_file in config_files:
            config = ConfigWorkflow(cfg_file.body, file_name=cfg_file.name)
            config.append_repo_details(
                repo_owner=repo_owner, repo_name=repo_name, head_sha=head_sha, branch_ref=branch_ref
            )
            if not config.is_triggered_by_event(event=event_type, branch=branch_ref):
                if event_type in config.trigger:
                    # there is a trigger for this event, but it is not matched
                    await post_gh_run_status_not_triggered(
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
                if event_type == "check_run" and config_run.check_name != payload["check_run"]["name"]:
                    logging.debug(
                        log_prefix + f"Skipping config '{cfg_file.name}' for check '{payload['check_run']['name']}'"
                        " because it is not triggered by this check's re-run."
                    )
                    continue
                task.update({"phase": TaskPhase.START_JOB.value, "run_config": config_run.to_dict()})
                push_to_redis(redis_client, task)
                count += 1
            logging.info(log_prefix + f"Enqueued {count} jobs for config '{cfg_file.name}'")
        return

    # =====================================================
    # JOB INITIALIZATION
    # This stage creates and launches new Lightning AI jobs based on processed configurations.
    # =====================================================
    if task_phase == TaskPhase.START_JOB:
        config_run = ConfigRun(**task["run_config"])
        debug_mode = config_run.mode == "debug"
        logging.info(log_prefix + f"Starting litJob for config '{config_run.name}'")
        run_name = config_run.check_name
        link_lightning_jobs = f"{LIGHTNING_CLOUD_URL}/{this_teamspace().owner.name}/{this_teamspace().name}/jobs/"
        check_id = await post_gh_run_status_create_check(
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
            logging.error(f"Failed to run job '{job_name}': {ex!s}\n{traceback.format_exc()}")
            text = f"```console\n{ex!s}\n```" if debug_mode else "No specific error details available."
            await post_gh_run_status_update_check(
                gh=gh,
                gh_url=url_check_id,
                title="Failed to run litJob",
                run_status=GitHubRunStatus.COMPLETED,
                run_conclusion=GitHubRunConclusion.FAILURE,
                url_job=link_lightning_jobs,
                summary=f"Job **{job_name}** failed.",
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
        await post_gh_run_status_update_check(
            gh=gh,
            gh_url=url_check_id,
            title="Job is pending",
            run_status=GitHubRunStatus.QUEUED,
            started_at=task["job_start_time"],
            summary=f"Job **{job_name}** is waiting for machine availability",
        )
        push_to_redis(redis_client, task)
        logging.info(log_prefix + f"Enqueued litJob for config '{config_run.name}'")
        return

    # =====================================================
    # JOB MONITORING
    # This stage continuously monitors running job status and handles timeout scenarios.
    # =====================================================
    if task_phase == TaskPhase.WAIT_JOB:
        job_name = task["job_name"]
        lit_job = _restore_lit_job_from_task(job_ref=task["job_reference"])
        if lit_job.status not in LIT_STATUS_RUNNING_OR_FINISHED:
            task = await process_job_pending(gh=gh, task=task, lit_job=lit_job)
            if TaskPhase(task["phase"]) == TaskPhase.FAILURE:
                logging.warning(log_prefix + f"Job '{job_name}' didn't start within the provided timeout.")
            else:
                logging.debug(log_prefix + f"Job '{job_name}' still pending, re-enqueued")
            push_to_redis(redis_client, task)
            return

        if lit_job.status in LIT_STATUS_RUNNING:
            task = await process_job_running(gh=gh, task=task, lit_job=lit_job)
            if TaskPhase(task["phase"]) == TaskPhase.FAILURE:
                logging.warning(log_prefix + f"Job '{job_name}' didn't finish within the provided timeout.")
            else:
                logging.debug(log_prefix + f"Job '**{job_name}**' still running, re-enqueued")
            push_to_redis(redis_client, task)
            return

        assert lit_job.status in LIT_STATUS_FINISHED, f"'{lit_job.status}' is not a finished as `{LIT_STATUS_FINISHED}`"
        # Update status to QUEUED, so it will be processed in the next iteration
        task.update({"phase": TaskPhase.RESULTS.value})
        push_to_redis(redis_client, task)
        logging.info(log_prefix + f"Enqueued results for job '{job_name}'")
        return

    # =====================================================
    # RESULTS PROCESSING
    # This stage finalizes job execution by processing results and updating GitHub status checks.
    # =====================================================
    if task_phase == TaskPhase.RESULTS:
        config_run = ConfigRun(**task["run_config"])
        debug_mode = config_run.mode == "debug"
        job_name = task["job_name"]
        lit_job = _restore_lit_job_from_task(job_ref=task["job_reference"])
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
        await post_gh_run_status_update_check(
            gh=gh,
            gh_url=url_check_id,
            title="Job finished",
            run_status=GitHubRunStatus.COMPLETED,
            run_conclusion=run_conclusion,
            started_at=task["job_start_time"],
            url_job=lit_job.link + "&job_detail_tab=logs",
            summary=f"Job **{job_name}** finished as `{job_status}` with exit code `{exit_code}`.",
            text=f"```console\n{results or 'No results available'}\n```",
        )
        logging.info(log_prefix + f"Job '{job_name}' finished as `{job_status}` with exit code `{exit_code}`.")
        lit_job.delete()
        return

    # =====================================================
    # FAILURE HANDLING
    # This stage manages job failure scenarios and cleanup operations for incomplete executions.
    # =====================================================
    if task_phase == TaskPhase.FAILURE:
        job_name = task["job_name"]
        lit_job = _restore_lit_job_from_task(job_ref=task["job_reference"])
        if lit_job.status in LIT_STATUS_FINISHED:
            # todo: some extra staff if required
            # lit_job.delete()
            return
        push_to_redis(redis_client, task)
        logging.debug(log_prefix + f"Job '{job_name}' still stopping, re-enqueued")
        return

    raise RuntimeError(f"Unknown task phase: {task_phase}")
