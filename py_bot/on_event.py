import asyncio
import datetime
import shutil

import aiohttp
from gidgethub.aiohttp import GitHubAPI

from py_bot.tasks import _download_repo_and_extract, run_repo_job, run_sleeping_task


async def on_pr_sync_simple(event, gh, *args, **kwargs) -> None:
    owner = event.data["repository"]["owner"]["login"]
    repo = event.data["repository"]["name"]
    head_sha = event.data["pull_request"]["head"]["sha"]
    print(f"-> pull_request: synchronize -> {owner=} {repo=} {head_sha=}")

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


async def on_pr_synchronize(event, gh, token, *args, **kwargs) -> None:
    owner = event.data["repository"]["owner"]["login"]
    repo = event.data["repository"]["name"]
    head_sha = event.data["pull_request"]["head"]["sha"]

    # 1) Download the repository at the specified ref
    repo_dir = await _download_repo_and_extract(owner, repo, head_sha, token)
    if not repo_dir:
        raise RuntimeError(f"Failed to download or extract repo {owner}/{repo} at {head_sha}")

    # 2) Launch check runs for each job
    tasks = []
    for i in range(3):
        task_name = f"PR Extra Task {i + 1}"
        print(f"=> pull_request: synchronize -> {task_name=}")

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
        tasks.append(asyncio.create_task(run_and_complete(token, owner, repo, head_sha, repo_dir, task_name, check_id)))

    # 3) Wait for all tasks to complete
    await asyncio.gather(*tasks)
    # 4) Cleanup the repo directory
    shutil.rmtree(repo_dir, ignore_errors=True)


async def run_and_complete(token, owner, repo, ref, repo_dir, task_name, check_id):
    # run the job with docker in the repo directory
    success, summary = await run_repo_job(repo_dir, f"ci-run_{owner}-{repo}-{ref}-{task_name}")

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
                    "summary": summary[:64000],
                },
            },
        )
