import asyncio
import datetime
import logging
import shutil
from pathlib import Path
from typing import Any

from aiohttp import ClientSession
from gidgethub.aiohttp import GitHubAPI
from lightning_sdk import Status, Teamspace
from lightning_sdk.lightning_cloud.env import LIGHTNING_CLOUD_URL

from py_bot.tasks import download_repo_and_extract, finalize_job, run_repo_job
from py_bot.utils import generate_matrix_from_config, is_triggered_by_event, load_configs_from_folder

JOB_QUEUE_TIMEOUT = 60 * 60  # 1 hour
JOB_QUEUE_INTERVAL = 10  # 10 seconds
STATUS_RUNNING_OR_FINISHED = {Status.Running, Status.Stopping, Status.Completed, Status.Stopped, Status.Failed}

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
    post_check = f"/repos/{repo_owner}/{repo_name}/check-runs"

    # 1) Download the repository at the specified ref
    repo_dir = await download_repo_and_extract(
        repo_owner=repo_owner, repo_name=repo_name, ref=head_sha, token=token, suffix=f"-event-{event.delivery_id}"
    )
    if not repo_dir.is_dir():
        raise RuntimeError(f"Failed to download or extract repo {repo_owner}/{repo_name} at {head_sha}")

    # 2) Read the config file
    repo_dir = Path(repo_dir).resolve()
    config_dir = repo_dir / ".lightning" / "workflows"
    configs = load_configs_from_folder(config_dir)
    if not configs:
        logging.warn(f"No valid configs found in {config_dir}")
        await gh.post(
            post_check,
            data={
                "name": "Lit bot",
                "head_sha": head_sha,
                "status": "completed",
                "conclusion": "skipped",
                "started_at": datetime.datetime.utcnow().isoformat() + "Z",
                "output": {
                    "title": "No Configs Found",
                    "summary": "No valid configuration files found in `.lightning/workflows`.",
                },
            },
        )
        shutil.rmtree(repo_dir, ignore_errors=True)
        return

    # 2) Launch check runs for each job
    tasks = []
    for cfg_file_name, config in configs:
        cfg_name = config.get("name", "Lit Job")
        if not is_triggered_by_event(event=event.event, branch=branch_ref, trigger=config.get("trigger")):
            if event.event in config.get("trigger", []):
                # there is a trigger for this event, but it is not matched
                await gh.post(
                    post_check,
                    data={
                        "name": f"{cfg_file_name} / {cfg_name} [{event.event}]",
                        "head_sha": head_sha,
                        "status": "completed",
                        "conclusion": "skipped",
                        "started_at": datetime.datetime.utcnow().isoformat() + "Z",
                        "output": {
                            "title": "Skipped",
                            "summary": f"Configuration `{cfg_file_name}` is not triggered"
                            f" by the event `{event.event}` on branch `{branch_ref}`.",
                        },
                    },
                )
            # skip this config if it is not triggered by the event
            continue
        parameters = generate_matrix_from_config(config.get("parametrize", {}))
        for _, params in enumerate(parameters):
            name = params.get("name") or cfg_name
            task_name = f"{cfg_file_name} / {name} ({', '.join([p or 'n/a' for p in params.values()])})"
            logging.debug(f"=> pull_request: synchronize -> {task_name=}")
            # Create a check run
            check = await gh.post(
                post_check,
                data={
                    "name": task_name,
                    "head_sha": head_sha,
                    "status": "queued",
                    "details_url": link_lightning_jobs,
                },
            )
            job_name = f"ci-run_{repo_owner}-{repo_name}-{head_sha}-{task_name.replace(' ', '_')}"
            post_check_id = f"/repos/{repo_owner}/{repo_name}/check-runs/{check['id']}"

            # detach with only the token, owner, repo, etc.
            tasks.append(
                asyncio.create_task(
                    run_and_complete(
                        token=token,
                        post_check=post_check_id,
                        job_name=job_name,
                        cfg_file_name=cfg_file_name,
                        config=config,
                        params=params,
                        repo_dir=repo_dir,
                    )
                )
            )

    # 3) Wait for all tasks to complete
    await asyncio.gather(*tasks)
    # 4) Cleanup the repo directory
    shutil.rmtree(repo_dir, ignore_errors=True)


# Decorator to inject ClientSession
def with_aiohttp_session(func):
    async def wrapper(*args, **kwargs):
        async with ClientSession() as session:
            return await func(*args, session=session, **kwargs)

    return wrapper


@with_aiohttp_session
async def run_and_complete(
    token: str,
    post_check: str,
    job_name: str,
    cfg_file_name: str,
    config: dict,
    params: dict,
    repo_dir: str | Path,
    session: ClientSession = None,
) -> None:
    debug_mode = config.get("mode", "info") == "debug"
    # open its own session & GitHubAPI to patch the check-run
    gh_api = GitHubAPI(session, "pr-check-bot", oauth_token=token)
    # define initial values
    this_teamspace = Teamspace()
    success = None  # Indicates whether the job succeeded, continue flow while it is None
    summary = ""  # Summary of the job's execution
    results = ""  # Full output of the job, if any
    job = None  # Placeholder for the job object
    job_url = f"{LIGHTNING_CLOUD_URL}/{this_teamspace.owner.name}/{this_teamspace.name}/jobs/"  # Link to all jobs
    cutoff_str = ""  # String used for cutoff processing
    try:
        job, cutoff_str = await run_repo_job(
            cfg_file_name=cfg_file_name, config=config, params=params, repo_dir=repo_dir, job_name=job_name
        )
    except Exception as ex:
        success, summary = False, f"Job `{job_name}` failed."
        if debug_mode:
            results = f"{ex!s}"
        else:
            logging.error(f"Failed to run job `{job_name}`: {ex!s}")
    if success is None:
        job_url = job.link + "&job_detail_tab=logs"
        await gh_api.patch(
            post_check,
            data={
                "status": "queued",
                "output": {
                    "title": "Job is pending",
                    "summary": "Wait for machine availability",
                },
                "details_url": job_url,
            },
        )
        queue_start = asyncio.get_event_loop().time()
        while True:
            if job.status in STATUS_RUNNING_OR_FINISHED:
                break
            if asyncio.get_event_loop().time() - queue_start > JOB_QUEUE_TIMEOUT:
                success, summary = (
                    False,
                    f"Job `{job_name}` didn't start within the provided ({JOB_QUEUE_TIMEOUT}) timeout.",
                )
                job.stop()
                break
            await asyncio.sleep(JOB_QUEUE_INTERVAL)
    if success is None:
        await gh_api.patch(
            post_check,
            data={
                "status": "in_progress",
                "started_at": datetime.datetime.utcnow().isoformat() + "Z",
                "output": {
                    "title": "Job is running",
                    "summary": "Job is running on Lightning Cloud, please wait until it finishes.",
                },
                "details_url": job_url,
            },
        )
        try:
            timeout_minutes = float(config.get("timeout", 60))  # the default timeout is 60 minutes
            await job.async_wait(timeout=timeout_minutes * 60)  # wait for the job to finish
            success, results = finalize_job(job, cutoff_str, debug=debug_mode)
            summary = f"Job `{job_name}` finished with {success}"
        except Exception as ex:  # most likely TimeoutError
            job.stop()  # todo: drop it when waiting will have arg `stop_on_timeout=True`
            success, summary = False, f"Job `{job_name}` failed"
            if debug_mode:
                results = f"{ex!s}"
            else:
                logging.error(f"Failed to run job `{job_name}`: {ex!s}")

    logging.debug(f"job '{job_name}' finished with {success}")
    await gh_api.patch(
        post_check,
        data={
            "status": "completed",
            "completed_at": datetime.datetime.utcnow().isoformat() + "Z",
            "conclusion": "success" if success else "failure",
            "output": {
                "title": "Job results",
                "summary": summary,
                # todo: consider improve parsing and formatting with MD
                "text": f"```console\n{results or 'No results available'}\n```",
            },
            "details_url": job_url,
        },
    )
