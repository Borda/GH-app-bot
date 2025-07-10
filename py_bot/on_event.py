import datetime
import os
import time
from pathlib import Path

import aiohttp
import jwt  # PyJWT
from gidgethub.aiohttp import GitHubAPI
from gidgethub.apps import get_installation_access_token

from py_bot.tasks import run_repo_job, run_sleeping_task


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


async def on_pr_synchronize(event, gh, token, *args, **kwargs):
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
            "started_at": datetime.datetime.utcnow().isoformat() + "Z",
        },
    )
    check_id = check["id"]

    # 2) Download code & run your logic
    success, summary = await run_repo_job(owner, repo, head_sha, token)
    print(f"Task finished >> {success}")

    # 3) Complete the check run
    conclusion = "success" if success else "failure"
    await gh.patch(
        f"/repos/{owner}/{repo}/check-runs/{check_id}",
        data={
            "status": "completed",
            "completed_at": datetime.datetime.utcnow().isoformat() + "Z",
            "conclusion": conclusion,
            "output": {
                "title": "Extra Task Results",
                "summary": summary or "Bot is still thinking what just happened...",
            },
        },
    )
