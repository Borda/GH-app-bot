import asyncio
import io
import os
import shutil
import uuid
import zipfile
from pathlib import Path

import aiohttp
import yaml
from lightning_sdk import Job, Machine, Status


async def run_sleeping_task(event):
    # Replace it with real logic; here we just succeed
    await asyncio.sleep(60)
    return True


async def _download_repo_and_extract(owner, repo, ref, token) -> str:
    # 1) Fetch zipball archive
    url = f"https://api.github.com/repos/{owner}/{repo}/zipball/{ref}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    async with aiohttp.ClientSession() as session, session.get(url, headers=headers) as resp:
        resp.raise_for_status()
        archive_data = await resp.read()
    print(f"Pull repo from {url}")

    # 2) Extract zip into a temp directory
    tempdir = Path(".temp") / uuid.uuid4().hex
    with zipfile.ZipFile(io.BytesIO(archive_data)) as zf:
        zf.extractall(tempdir)

    # 3) Locate the extracted root folder
    children = os.listdir(tempdir)
    if not children:
        return ""
    return os.path.join(tempdir, children[0])


async def run_repo_job(owner, repo, ref, token):
    """
    Download the full repo at `ref` into a tempdir, look for config file and execute the job.
    """
    repo_root = await _download_repo_and_extract(owner, repo, ref, token)
    if not repo_root:
        return False, f"Failed to download or extract repo {owner}/{repo} at {ref}"

    # Try to load the config file
    cfg_path = os.path.join(repo_root, ".lightning/actions.yaml")
    if not os.path.exists(cfg_path):
        return False, f"No config found at {cfg_path!r}"

    try:
        with open(cfg_path) as f:
            config = yaml.safe_load(f)
        print(f">>>>>>>>>>>>>>\n{config!r}\n<<<<<<<<<<<<<<\n")
    except Exception as e:
        return False, f"Error parsing config: {e!s}"

    cmd = f"ls -lh && ls -lh .temp/ cd {repo_root} && pwd && {config}"
    print(f"CMD: {cmd}")
    job = Job.run(
        name=f"ci-run_{owner}-{repo}-{ref}",
        command=cmd,
        machine=Machine.CPU,
        # interruptible=True,
    )
    job.wait()
    shutil.rmtree(os.path.dirname(repo_root), ignore_errors=True)

    success = job.status == Status.Completed
    return success, f"run finished as {job.status}\n{job.logs}"
