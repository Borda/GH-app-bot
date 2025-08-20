import asyncio
import logging
import shutil
from collections.abc import Callable
from datetime import datetime
from functools import lru_cache, partial
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout
from gidgethub.aiohttp import GitHubAPI
from lightning_sdk import Status, Teamspace
from lightning_sdk.lightning_cloud.env import LIGHTNING_CLOUD_URL

from _bots_commons.configs import ConfigFile, ConfigRun, ConfigWorkflow, GitHubRunConclusion, GitHubRunStatus
from _bots_commons.downloads import download_repo_and_extract
from _bots_commons.lit_job import finalize_job, job_run
from _bots_commons.utils import (
    extract_repo_details,
    gh_patch_with_retry,
    gh_post_with_retry,
    wrap_long_text,
)

JOB_QUEUE_TIMEOUT = 60 * 60  # 1 hour
JOB_QUEUE_INTERVAL = 10  # 10 seconds
STATUS_RUNNING_OR_FINISHED = {Status.Running, Status.Stopping, Status.Completed, Status.Stopped, Status.Failed}
MAX_OUTPUT_LENGTH = 65525  # GitHub API limit for check-run output.text
LOCAL_ROOT_DIR = Path(__file__).parent
LOCAL_TEMP_DIR = LOCAL_ROOT_DIR / ".temp"


@lru_cache
def this_teamspace() -> Teamspace:
    """Get the current Teamspace instance."""
    return Teamspace()


# async def on_pr_sync_simple(event, gh, *args: Any, **kwargs: Any) -> None:
#     repo_owner = event.data["repository"]["owner"]["login"]
#     repo_name = event.data["repository"]["name"]
#     head_sha = event.data["pull_request"]["head"]["sha"]
#     logging.debug(f"-> pull_request: synchronize -> {repo_owner=} {repo_name=} {head_sha=}")
#
#     # 1) Create an in_progress check run
#     check = await gh.post(
#         f"/repos/{repo_owner}/{repo_name}/check-runs",
#         data={
#             "name": "PR Extra Task",
#             "head_sha": head_sha,
#             "status": "in_progress",
#             "started_at": datetime.utcnow().isoformat() + "Z",
#         },
#     )
#     check_id = check["id"]
#
#     # 2) Run your custom task (e.g., lint, tests…)
#     success = await run_sleeping_task(event)
#
#     # 3) Complete the check run
#     conclusion = "success" if success else "failure"
#     await gh.patch(
#         f"/repos/{repo_owner}/{repo_name}/check-runs/{check_id}",
#         data={
#             "status": "completed",
#             "completed_at": datetime.utcnow().isoformat() + "Z",
#             "conclusion": conclusion,
#             "output": {
#                 "title": "Extra Task Results",
#                 "summary": "All checks passed!" if success else "Some checks failed.",
#             },
#         },
#     )


async def on_code_changed(event, gh, token: str, *args: Any, **kwargs: Any) -> None:
    """Handle GitHub webhook events for code changes (push or pull_request)."""
    # figure out the commit SHA and branch ref
    repo_owner, repo_name, head_sha, branch_ref = extract_repo_details(event.event, event.data)
    link_lightning_jobs = f"{LIGHTNING_CLOUD_URL}/{this_teamspace().owner.name}/{this_teamspace().name}/jobs/"
    # Create a partial function for posting check runs
    post_check = partial(gh_post_with_retry, gh=gh, url=f"/repos/{repo_owner}/{repo_name}/check-runs")
    config_files, config_error = [], None
    log_prefix = f"{repo_owner}/{repo_name}::{head_sha[:7]}::{event.event}::\t"

    # 1) Download the repository at the specified ref
    repo_dir = await download_repo_and_extract(
        repo_owner=repo_owner,
        repo_name=repo_name,
        git_ref=head_sha,
        auth_token=token,
        folder_path=LOCAL_TEMP_DIR,
        # extract only the `.lightning` subfolder
        subfolder=".lightning",  # extract only `.lightning` subfolder
        suffix=f"event-{event.delivery_id}",
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
        config_dir = None
        logging.warn(log_prefix + "Failed to extract `.lightning` folder from repo.")

    if not config_files:
        logging.warn(log_prefix + f"No valid configs found in {config_dir}")
        text_error = f"```console\n{config_error!s}\n```" if config_error else "No specific error details available."
        await post_check(
            data={
                "name": "Lit bot",
                "head_sha": head_sha,
                "status": "completed",
                "conclusion": "skipped",
                "started_at": datetime.utcnow().isoformat() + "Z",
                "output": {
                    "title": "No Configs Found",
                    "summary": "No valid configuration files found in `.lightning/workflows` folder.",
                    "text": text_error,
                },
            },
        )
        return

    # 3) Launch check runs for each job
    tasks = []
    for cfg_file in config_files:
        config = ConfigWorkflow(cfg_file.body, file_name=cfg_file.name)
        config.append_repo_details(
            repo_owner=repo_owner,
            repo_name=repo_name,
            head_sha=head_sha,
            branch_ref=branch_ref,
        )
        # cfg_name = cfg_file.get("name", "Lit Job")
        # cfg_trigger = cfg_file.get("trigger", {})
        if not config.is_triggered_by_event(event=event.event, branch=branch_ref):
            if event.event in config.trigger:
                # there is a trigger for this event, but it is not matched
                await post_check(
                    data={
                        "name": f"{cfg_file.name} / {config.name} [{event.event}]",
                        "head_sha": head_sha,
                        "status": "completed",
                        "conclusion": "skipped",
                        "started_at": datetime.utcnow().isoformat() + "Z",
                        "output": {
                            "title": "Skipped",
                            "summary": f"Configuration `{cfg_file.name}` is not triggered"
                            f" by the event `{event.event}` on branch `{branch_ref}` (with `{config.trigger}`).",
                        },
                    },
                )
            # skip this config if it is not triggered by the event
            logging.info(
                log_prefix + f"Skipping config {cfg_file.name} for event '{event.event}' on branch '{branch_ref}'"
                f" because it is not triggered by this event with `{config.trigger}`."
            )
            continue
        for config_run in config.generate_runs():
            run_params = [p or "n/a" for p in config_run.params.values()]
            task_name = f"{config_run.file_name} / {config_run.name} ({', '.join(run_params)})"
            logging.debug(log_prefix + f"=> pull_request: synchronize -> {task_name=}")
            # Create a check run
            check = await post_check(
                data={"name": task_name, "head_sha": head_sha, "status": "queued", "details_url": link_lightning_jobs},
            )
            job_name = (
                f"ci-run_{repo_owner}-{repo_name}-{head_sha[:7]}"
                f"-event-{event.delivery_id.split('-')[0]}"
                f"-{task_name.replace(' ', '_')}"
            )
            post_check_id = f"/repos/{repo_owner}/{repo_name}/check-runs/{check['id']}"
            patch_this_check_run = partial(patch_check_run, token=token, url=post_check_id)

            # detach with only the token, owner, repo, etc.
            tasks.append(
                asyncio.create_task(
                    complete_run(
                        fn_patch_check_run=patch_this_check_run,
                        job_name=job_name,
                        config_run=config_run,
                        token=token,
                    )
                )
            )

    # 4) Wait for all tasks to complete
    logging.info(log_prefix + f"Waiting for {len(tasks)} tasks to complete...")
    await asyncio.gather(*tasks)
    results = await asyncio.gather(*tasks)
    logging.info(log_prefix + f"All tasks completed: {results}")


async def patch_check_run(token: str, url: str, data: dict, retries: int = 3, backoff: float = 1.0) -> Any:
    """Patch a check run with retries in case of connection issues."""
    timeout = ClientTimeout(total=retries)
    async with ClientSession(timeout=timeout) as session:
        gh_api = GitHubAPI(session, "bot_async_tasks", oauth_token=token)
        return gh_patch_with_retry(gh_api, url, data, retries=retries, backoff=backoff)


async def complete_run(
    fn_patch_check_run: Callable,
    job_name: str,
    config_run: ConfigRun,
    token: str,
) -> GitHubRunConclusion:
    """Run a job and update the check run status."""
    debug_mode = config_run.mode == "debug"
    # define initial values
    summary = ""  # Summary of the job's execution
    results = ""  # Full output of the job, if any
    job = None  # Placeholder for the job object
    run_status = GitHubRunStatus.QUEUED
    run_conclusion = GitHubRunConclusion.NEUTRAL
    url_job = ""  # URL to the job in Lightning Cloud
    url_job_table = (
        f"{LIGHTNING_CLOUD_URL}/{this_teamspace().owner.name}/{this_teamspace().name}/jobs/"  # Link to all jobs
    )
    logs_separator, exit_separator = "", ""  # String used for cutoff processing

    try:
        job, logs_separator, exit_separator = await job_run(
            cfg_file_name=config_run.file_name, config=config_run, gh_token=token, job_name=job_name
        )
    except Exception as ex:
        run_status, run_conclusion = GitHubRunStatus.COMPLETED, GitHubRunConclusion.FAILURE
        summary = f"Job `{job_name}` failed."
        if debug_mode:
            results = f"{ex!s}"
        else:
            logging.error(f"Failed to run job `{job_name}`: {ex!s}")

    if run_status == GitHubRunStatus.QUEUED:
        url_job = job.link + "&job_detail_tab=logs"
        await fn_patch_check_run(
            data={
                "status": run_status.value,
                "output": {
                    "title": "Job is pending",
                    "summary": "Wait for machine availability",
                },
                "details_url": url_job or url_job_table,
            },
        )
        queue_start = asyncio.get_event_loop().time()
        while True:
            if job.status in STATUS_RUNNING_OR_FINISHED:
                break
            if asyncio.get_event_loop().time() - queue_start > JOB_QUEUE_TIMEOUT:
                run_status, run_conclusion = GitHubRunStatus.COMPLETED, GitHubRunConclusion.CANCELLED
                summary = f"Job `{job_name}` didn't start within the provided ({JOB_QUEUE_TIMEOUT}) timeout."
                job.stop()
                break
            await asyncio.sleep(JOB_QUEUE_INTERVAL)
        run_status = GitHubRunStatus.IN_PROGRESS

    if run_status == GitHubRunStatus.IN_PROGRESS:
        await fn_patch_check_run(
            data={
                "status": run_status.value,
                "started_at": datetime.utcnow().isoformat() + "Z",
                "output": {
                    "title": "Job is running",
                    "summary": "Job is running on Lightning Cloud, please wait until it finishes.",
                },
                "details_url": url_job or url_job_table,
            },
        )
        try:
            await job.async_wait(
                timeout=config_run.timeout_minutes * 60, stop_on_timeout=True
            )  # wait for the job to finish
            job_status, exit_code, results = finalize_job(
                job, logs_hash=logs_separator, exit_hash=exit_separator, debug=debug_mode
            )
            run_status = GitHubRunStatus.COMPLETED
            if exit_code is None:  # if the job didn't return an exit code
                run_conclusion = GitHubRunConclusion.NEUTRAL
            elif exit_code == 0:  # if the job finished successfully
                run_conclusion = GitHubRunConclusion.SUCCESS
            else:  # if the job failed
                run_conclusion = GitHubRunConclusion.FAILURE
            summary = f"Job `{job_name}` finished as {job_status} with exit code {exit_code}."
        except TimeoutError:
            run_status, run_conclusion = GitHubRunStatus.COMPLETED, GitHubRunConclusion.CANCELLED
            summary = f"Job `{job_name}` cancelled due to timeout after {config_run.timeout_minutes} minutes."
            if debug_mode:
                results = "Job timed out, no results available."
            else:
                logging.warning(f"Job `{job_name}` timed out after {config_run.timeout_minutes} minutes")
        except Exception as ex:
            run_status, run_conclusion = GitHubRunStatus.COMPLETED, GitHubRunConclusion.ACTION_REQUIRED
            summary = f"Job `{job_name}` failed due to an unexpected error: {ex!s}"
            if debug_mode:
                results = f"{ex!s}"
            else:
                logging.error(f"Failed to run job `{job_name}`: {ex!s}")

    logging.info(
        f"Job finished with {run_conclusion} >>> {url_job or (url_job_table + ' search for name ' + job_name)}"
    )
    results = wrap_long_text(results, text_length=MAX_OUTPUT_LENGTH - 20)  # wrap the results to fit in the output
    await fn_patch_check_run(
        data={
            "status": run_status.value,
            "completed_at": datetime.utcnow().isoformat() + "Z",
            "conclusion": run_conclusion.value,
            "output": {
                "title": "Job results",
                "summary": summary,
                # todo: upload the full results as artifact
                "text": f"```console\n{results or 'No results available'}\n```",
            },
            "details_url": url_job or url_job_table,
        },
    )
    return run_conclusion
