import asyncio
import datetime
import logging
import shutil
from typing import Any

import aiohttp
from gidgethub.aiohttp import GitHubAPI

from py_bot.tasks import _download_repo_and_extract, run_repo_job, run_sleeping_task
from py_bot.utils import generate_matrix_from_config, load_configs_from_folder

MAX_SUMMARY_LENGTH = 64000


async def on_pr_sync_simple(event, gh, *args: Any, **kwargs: Any) -> None:
    owner = event.data["repository"]["owner"]["login"]
    repo = event.data["repository"]["name"]
    head_sha = event.data["pull_request"]["head"]["sha"]
    logging.debug(f"-> pull_request: synchronize -> {owner=} {repo=} {head_sha=}")

    # 1) Create an in_progress check run
    check = await gh.post(
        f"/repos/{owner}/{repo}/check-runs",
        data={
            "name": "PR Extra Task",
            "head_sha": head_sha,
            "status": "in_progress",
            "started_at": datetime.utcnow().isoformat() + "Z",
        },
    )
    check_id = check["id"]

    # 2) Run your custom task (e.g., lint, tests…)
    success = await run_sleeping_task(event)

    # 3) Complete the check run
    conclusion = "success" if success else "failure"
    await gh.patch(
        f"/repos/{owner}/{repo}/check-runs/{check_id}",
        data={
            "status": "completed",
            "completed_at": datetime.utcnow().isoformat() + "Z",
            "conclusion": conclusion,
            "output": {
                "title": "Extra Task Results",
                "summary": "All checks passed!" if success else "Some checks failed.",
            },
        },
    )


async def on_pr_synchronize(event, gh, token: str, *args: Any, **kwargs: Any) -> None:
    owner = event.data["repository"]["owner"]["login"]
    repo = event.data["repository"]["name"]
    head_sha = event.data["pull_request"]["head"]["sha"]

    # 1) Download the repository at the specified ref
    repo_dir = await _download_repo_and_extract(owner, repo, head_sha, token)
    if not repo_dir:
        raise RuntimeError(f"Failed to download or extract repo {owner}/{repo} at {head_sha}")

    # 2) Read the config file
    configs = load_configs_from_folder(repo_dir)

    # 2) Launch check runs for each job
    tasks = []
    for cfg_file, config in configs:
        parameters = generate_matrix_from_config(config.get("parametrize", {}))
        for i, params in enumerate(parameters):
            name = params.get("name") or config.get("name", "Lit Job")
            task_name = f"{cfg_file} / {name} ({', '.join(params.values())})"
            logging.debug(f"=> pull_request: synchronize -> {task_name=}")

            # Create check run
            check = await gh.post(
                f"/repos/{owner}/{repo}/check-runs",
                data={
                    "name": task_name,
                    "head_sha": head_sha,
                    "status": "in_progress",
                    "started_at": datetime.datetime.utcnow().isoformat() + "Z",
                },
            )
            check_id = check["id"]

            # detach with only the token, owner, repo, etc.
            tasks.append(
                asyncio.create_task(
                    run_and_complete(
                        token=token,
                        owner=owner,
                        repo=repo,
                        ref=head_sha,
                        config=config,
                        params=params,
                        repo_dir=repo_dir,
                        task_name=task_name,
                        check_id=check_id,
                    )
                )
            )

    # 3) Wait for all tasks to complete
    await asyncio.gather(*tasks)
    # 4) Cleanup the repo directory
    shutil.rmtree(repo_dir, ignore_errors=True)


async def run_and_complete(
    token, owner: str, repo: str, ref: str, config: dict, params: dict, repo_dir: str, task_name: str, check_id
) -> None:
    # run the job with docker in the repo directory
    job_name = f"ci-run_{owner}-{repo}-{ref}-{task_name.replace(' ', '_')}"
    success, summary = await run_repo_job(config=config, params=params, repo_dir=repo_dir, job_name=job_name)
    logging.debug(f"job '{job_name}' finished with {success}")

    # open its own session & GitHubAPI to patch the check-run
    async with aiohttp.ClientSession() as session:
        gh2 = GitHubAPI(session, "pr-check-bot", oauth_token=token)
        await gh2.patch(
            f"/repos/{owner}/{repo}/check-runs/{check_id}",
            data={
                "status": "completed",
                "completed_at": datetime.datetime.utcnow().isoformat() + "Z",
                "conclusion": "success" if success else "failure",
                "output": {
                    "title": f"{task_name} result",
                    "summary": summary[:MAX_SUMMARY_LENGTH],
                },
            },
        )
