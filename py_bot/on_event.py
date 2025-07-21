import asyncio
import datetime
import logging
import shutil
from collections.abc import Callable
from enum import Enum
from functools import partial
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout, client_exceptions
from gidgethub.aiohttp import GitHubAPI
from lightning_sdk import Status, Teamspace
from lightning_sdk.lightning_cloud.env import LIGHTNING_CLOUD_URL

from py_bot.tasks import download_repo_archive, extract_zip_archive, finalize_job, run_repo_job
from py_bot.utils import generate_matrix_from_config, is_triggered_by_event, load_configs_from_folder

JOB_QUEUE_TIMEOUT = 60 * 60  # 1 hour
JOB_QUEUE_INTERVAL = 10  # 10 seconds
STATUS_RUNNING_OR_FINISHED = {Status.Running, Status.Stopping, Status.Completed, Status.Stopped, Status.Failed}
MAX_OUTPUT_LENGTH = 65525  # GitHub API limit for check-run output.text
LOCAL_ROOT_DIR = Path(__file__).parent
LOCAL_TEMP_DIR = LOCAL_ROOT_DIR / ".temp"


class GitHubRunStatus(Enum):
    """Enum for GitHub check run statuses."""

    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class GitHubRunConclusion(Enum):
    """Enum for GitHub check run conclusions."""

    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"
    NEUTRAL = "neutral"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    ACTION_REQUIRED = "action_required"


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


async def post_with_retry(gh, url: str, data: dict, retries: int = 3, backoff: float = 1.0) -> Any:
    """Post data to GitHub API with retries in case of connection issues."""
    for it in range(1, retries + 1):
        try:
            return await gh.post(url, data=data)
        except client_exceptions.ServerDisconnectedError:
            if it == retries:
                raise
            await asyncio.sleep(backoff * it)
    return None


async def on_code_changed(event, gh, token: str, *args: Any, **kwargs: Any) -> None:
    """Handle GitHub webhook events for code changes (push or pull_request)."""
    # figure out the commit SHA and branch ref
    if event.event == "push":
        head_sha = event.data["after"]
        branch_ref = event.data["ref"][len("refs/heads/") :]
    else:  # pull_request
        head_sha = event.data["pull_request"]["head"]["sha"]
        branch_ref = event.data["pull_request"]["base"]["ref"]
    repo_owner = event.data["repository"]["owner"]["login"]
    repo_name = event.data["repository"]["name"]
    this_teamspace = Teamspace()
    link_lightning_jobs = f"{LIGHTNING_CLOUD_URL}/{this_teamspace.owner.name}/{this_teamspace.name}/jobs/"
    # Create a partial function for posting check runs
    post_check = partial(post_with_retry, gh=gh, url=f"/repos/{repo_owner}/{repo_name}/check-runs")

    # 1) Download the repository at the specified ref
    archive_path = await download_repo_archive(
        repo_owner=repo_owner,
        repo_name=repo_name,
        ref=head_sha,
        token=token,
        folder_path=LOCAL_TEMP_DIR,
        suffix=f"event-{event.delivery_id}",
    )
    if not archive_path.is_file():
        raise RuntimeError(f"Failed to download repo {repo_owner}/{repo_name} at {head_sha}")
    repo_dir = extract_zip_archive(
        zip_path=archive_path,
        extract_to=LOCAL_TEMP_DIR,
        subfolder=".lightning"  # extract only `.lightning` subfolder
    )
    if not repo_dir.is_dir():
        raise RuntimeError(f"Failed to extract repo {repo_owner}/{repo_name} at {head_sha}")
    logging.info(f"Downloaded repo {repo_owner}/{repo_name} at {head_sha} to {repo_dir}")

    # 2) Read the config file
    repo_dir = Path(repo_dir).resolve()
    config_dir = repo_dir / ".lightning" / "workflows"
    configs, config_error = [], None
    try:
        configs = load_configs_from_folder(config_dir)
    except Exception as ex:
        config_error = ex
    if not configs:
        logging.warn(f"No valid configs found in {config_dir}")
        text_error = f"```console\n{config_error!s}\n```" if config_error else "No specific error details available."
        await post_check(
            data={
                "name": "Lit bot",
                "head_sha": head_sha,
                "status": "completed",
                "conclusion": "skipped",
                "started_at": datetime.datetime.utcnow().isoformat() + "Z",
                "output": {
                    "title": "No Configs Found",
                    "summary": "No valid configuration files found in `.lightning/workflows` folder.",
                    "text": text_error,
                },
            },
        )
        logging.info(f"Early cleaning up the repo directory: {repo_dir}")
        shutil.rmtree(repo_dir, ignore_errors=True)
        return

    # 2) Launch check runs for each job
    tasks = []
    for cfg_file_name, config in configs:
        cfg_name = config.get("name", "Lit Job")
        cfg_trigger = config.get("trigger", {})
        if not is_triggered_by_event(event=event.event, branch=branch_ref, trigger=cfg_trigger):
            if event.event in config.get("trigger", []):
                # there is a trigger for this event, but it is not matched
                await post_check(
                    data={
                        "name": f"{cfg_file_name} / {cfg_name} [{event.event}]",
                        "head_sha": head_sha,
                        "status": "completed",
                        "conclusion": "skipped",
                        "started_at": datetime.datetime.utcnow().isoformat() + "Z",
                        "output": {
                            "title": "Skipped",
                            "summary": f"Configuration `{cfg_file_name}` is not triggered"
                            f" by the event `{event.event}` on branch `{branch_ref}` (with {cfg_trigger}).",
                        },
                    },
                )
            # skip this config if it is not triggered by the event
            logging.info(
                f"Skipping config {cfg_file_name} for event {event.event} on branch {branch_ref}"
                f" because it is not triggered by this event with {cfg_trigger}."
            )
            continue
        parameters = generate_matrix_from_config(config.get("parametrize", {}))
        for _, params in enumerate(parameters):
            name = params.get("name") or cfg_name
            task_name = f"{cfg_file_name} / {name} ({', '.join([p or 'n/a' for p in params.values()])})"
            logging.debug(f"=> pull_request: synchronize -> {task_name=}")
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
                    run_and_complete(
                        fn_patch_check_run=patch_this_check_run,
                        job_name=job_name,
                        cfg_file_name=cfg_file_name,
                        config=config,
                        params=params,
                        repo_dir=repo_dir,
                    repo_archive=archive_path,
                    )
                )
            )

    # 3) Wait for all tasks to complete
    logging.info(f"Waiting for {len(tasks)} tasks to complete...")
    await asyncio.gather(*tasks)
    # 4) Cleanup the repo directory
    logging.info(f"Cleaning up the repo directory: {repo_dir}")
    shutil.rmtree(repo_dir, ignore_errors=True)


async def patch_check_run(token: str, url: str, data: dict, retries: int = 3, backoff: float = 1.0) -> Any:
    """Patch a check run with retries in case of connection issues."""
    timeout = ClientTimeout(total=60)  # allow up to 60 s to connect/send
    async with ClientSession(timeout=timeout) as session:
        gh_api = GitHubAPI(session, "pr-check-bot", oauth_token=token)
        for it in range(1, retries + 1):  # up to 3 attempts
            try:
                return await gh_api.patch(url, data=data)
            except (asyncio.TimeoutError, client_exceptions.ClientConnectorError):
                if it == retries:
                    raise
                await asyncio.sleep(it * backoff)
    return None


async def run_and_complete(
    fn_patch_check_run: Callable,
    job_name: str,
    cfg_file_name: str,
    config: dict,
    params: dict,
    repo_dir: str | Path, repo_archive: str | Path
) -> None:
    """Run a job and update the check run status."""
    debug_mode = config.get("mode", "info") == "debug"
    # define initial values
    this_teamspace = Teamspace()
    summary = ""  # Summary of the job's execution
    results = ""  # Full output of the job, if any
    job = None  # Placeholder for the job object
    run_status = GitHubRunStatus.QUEUED
    run_conclusion = GitHubRunConclusion.NEUTRAL
    url_job = ""  # URL to the job in Lightning Cloud
    url_job_table = f"{LIGHTNING_CLOUD_URL}/{this_teamspace.owner.name}/{this_teamspace.name}/jobs/"  # Link to all jobs
    cutoff_str = ""  # String used for cutoff processing

    try:
        job, cutoff_str = await run_repo_job(
            cfg_file_name=cfg_file_name, config=config, params=params, repo_dir=repo_dir, repo_archive=repo_archive, job_name=job_name
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
                run_status, run_conclusion = GitHubRunStatus.COMPLETED, GitHubRunConclusion.TIMED_OUT
                summary = f"Job `{job_name}` didn't start within the provided ({JOB_QUEUE_TIMEOUT}) timeout."
                job.stop()
                break
            await asyncio.sleep(JOB_QUEUE_INTERVAL)
        run_status = GitHubRunStatus.IN_PROGRESS

    if run_status == GitHubRunStatus.IN_PROGRESS:
        await fn_patch_check_run(
            data={
                "status": run_status.value,
                "started_at": datetime.datetime.utcnow().isoformat() + "Z",
                "output": {
                    "title": "Job is running",
                    "summary": "Job is running on Lightning Cloud, please wait until it finishes.",
                },
                "details_url": url_job or url_job_table,
            },
        )
        try:
            timeout_minutes = float(config.get("timeout", 60))  # the default timeout is 60 minutes
            await job.async_wait(timeout=timeout_minutes * 60)  # wait for the job to finish
            job_status, results = finalize_job(job, cutoff_str, debug=debug_mode)
            run_status = GitHubRunStatus.COMPLETED
            run_conclusion = (
                GitHubRunConclusion.SUCCESS if job_status == Status.Completed else GitHubRunConclusion.FAILURE
            )
            summary = f"Job `{job_name}` finished as {job_status}"
        except TimeoutError:
            job.stop()  # todo: drop it when waiting will have arg `stop_on_timeout=True`
            run_status, run_conclusion = GitHubRunStatus.COMPLETED, GitHubRunConclusion.CANCELLED
            summary = f"Job `{job_name}` cancelled due to timeout after {timeout_minutes} minutes."
            if debug_mode:
                results = "Job timed out, no results available."
            else:
                logging.warning(f"Job `{job_name}` timed out after {timeout_minutes} minutes")
        except Exception as ex:
            run_status, run_conclusion = GitHubRunStatus.COMPLETED, GitHubRunConclusion.FAILURE
            summary = f"Job `{job_name}` failed due to an unexpected error: {ex!s}"
            if debug_mode:
                results = f"{ex!s}"
            else:
                logging.error(f"Failed to run job `{job_name}`: {ex!s}")

    logging.info(
        f"Job finished with {run_conclusion} >>> {url_job or (url_job_table + ' search for name ' + job_name)}"
    )
    if len(results) > MAX_OUTPUT_LENGTH:
        results = results[: MAX_OUTPUT_LENGTH - 20] + "\n…(truncated)"
    await fn_patch_check_run(
        data={
            "status": run_status.value,
            "completed_at": datetime.datetime.utcnow().isoformat() + "Z",
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
