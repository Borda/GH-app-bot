import asyncio
import datetime
import os
import time
from pathlib import Path

import aiohttp
import jwt  # PyJWT
from gidgethub.aiohttp import GitHubAPI
from gidgethub.apps import get_installation_access_token

from py_bot.tasks import run_repo_job, run_sleeping_task, _download_repo_and_extract


async def on_pr_sync_simple(event, gh, *args, **kwargs):
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


# async def on_pr_synchronize(event, gh, token, *args, **kwargs):
#     owner = event.data["repository"]["owner"]["login"]
#     repo = event.data["repository"]["name"]
#     head_sha = event.data["pull_request"]["head"]["sha"]
#     print(f"-> pull_request: synchronize -> {owner=} {repo=} {head_sha=}")
#
#     # 1) Create an in_progress check run
#     check = await gh.post(
#         f"/repos/{owner}/{repo}/check-runs",
#         data={
#             "name": "PR Extra Task",
#             "head_sha": head_sha,
#             "status": "in_progress",
#             "started_at": datetime.datetime.utcnow().isoformat() + "Z",
#         },
#     )
#     check_id = check["id"]
#
#     # 2) Download code & run your logic
#     success, summary = await run_repo_job(owner, repo, head_sha, token)
#     print(f"Task finished >> {success}")
#
#     # 3) Complete the check run
#     conclusion = "success" if success else "failure"
#     await gh.patch(
#         f"/repos/{owner}/{repo}/check-runs/{check_id}",
#         data={
#             "status": "completed",
#             "completed_at": datetime.datetime.utcnow().isoformat() + "Z",
#             "conclusion": conclusion,
#             "output": {
#                 "title": "Extra Task Results",
#                 "summary": summary or "Bot is still thinking what just happened...",
#             },
#         },
#     )


async def on_pr_synchronize(event, gh, token, *args, **kwargs):
    owner = event.data["repository"]["owner"]["login"]
    repo = event.data["repository"]["name"]
    head_sha = event.data["pull_request"]["head"]["sha"]

    repo_dir = await _download_repo_and_extract(owner, repo, head_sha, token)
    if not repo_dir:
        return False, f"Failed to download or extract repo {owner}/{repo} at {head_sha}"

    # Launch check runs for each job
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

        print(f"---> pull_request: synchronize :: create task...")
        # detach with only the token, owner, repo, etc.
        asyncio.create_task(
            run_and_complete(token, owner, repo, head_sha, repo_dir, task_name, check_id)
        )
        print(f"---> pull_request: synchronize :: move to next one...")


async def run_and_complete(token, owner, repo, ref, repo_dir, task_name, check_id):
    # 1) run the script in Docker
    success, summary = await run_repo_job(repo_dir, f"ci-run_{owner}-{repo}-{ref}-{task_name}")

    # 2) open its own session & GitHubAPI to patch the check-run
    async with aiohttp.ClientSession() as session:
        gh2 = GitHubAPI(session, "pr-check-bot", oauth_token=token)
        await gh2.patch(
            f"/repos/{owner}/{repo}/check-runs/{check_id}",
            data={
                "status":       "completed",
                "completed_at": datetime.datetime.utcnow().isoformat() + "Z",
                "conclusion":   "success" if success else "failure",
                "output": {
                    "title":   f"{task_name} result",
                    "summary": summary[:64000],
                },
            },
        )
