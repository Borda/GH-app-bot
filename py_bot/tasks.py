import asyncio
import io
import logging
import os
import shlex
import zipfile
from pathlib import Path
from typing import Any

import aiohttp
from lightning_sdk import Job, Machine, Status

from py_bot.utils import generate_unique_hash

LOCAL_ROOT_DIR = Path(__file__).parent
LOCAL_TEMP_DIR = LOCAL_ROOT_DIR / ".temp"


async def run_sleeping_task(*args: Any, **kwargs: Any):
    # Replace it with real logic; here we just succeed
    await asyncio.sleep(60)
    return True


async def _download_repo_and_extract(owner: str, repo: str, ref: str, token: str) -> Path:
    """Download a GitHub repository at a specific ref (branch, tag, commit) and extract it to a temp directory."""
    # 1) Fetch zipball archive
    url = f"https://api.github.com/repos/{owner}/{repo}/zipball/{ref}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    async with aiohttp.ClientSession() as session, session.get(url, headers=headers) as resp:
        resp.raise_for_status()
        archive_data = await resp.read()
    logging.debug(f"Pull repo from {url}")

    # 2) Extract zip into a temp directory
    tempdir = LOCAL_TEMP_DIR.resolve()
    tempdir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(archive_data)) as zf:
        # 1) Grab the first entry in the archive’s name list
        first_path = zf.namelist()[0]  # e.g. "repo-owner-repo-sha1234abcd/"
        root_folder = first_path.split("/", 1)[0]
        # 2) Extract everything
        zf.extractall(tempdir)

    # 3) Locate the extracted root folder
    return tempdir / root_folder


async def run_repo_job(config: dict, params: dict, repo_dir: str, job_name: str):
    """Download the full repo at `ref` into a tempdir, look for config and execute the job."""
    # mandatory
    config_run = config["run"]
    # optional
    docker_run_machine = Machine.from_str(params.get("machine") or config.get("machine", "CPU"))
    docker_run_image = params.get("image") or config.get("image", "ubuntu:22.04")
    config_env = config.get("env", {})
    config_env.update(params)  # add params to env

    cmd_run = os.linesep.join(["ls -lah", config_run])
    # print(f"CMD: {cmd_run}")
    docker_run_script = f".lightning-actions-{generate_unique_hash()}.sh"
    cmd_path = os.path.join(repo_dir, docker_run_script)
    assert not os.path.isfile(cmd_path), "the expected actions script already exists"
    # dump the cmd to .lightning/actions.sh
    with open(cmd_path, "w", encoding="utf_8") as fp:
        fp.write(cmd_run + os.linesep)
    assert os.path.isfile(cmd_path), "missing the created actions script"
    await asyncio.sleep(3)  # todo: wait for the file to be written, likely Job sync issue
    docker_run_env = " ".join([f"-e {k}={shlex.quote(str(v))}" for k, v in config_env.items()])
    # set desired wrap width
    wrap_cmd = f"fold -w 120 -s"
    # list of core commands to run
    run_cmds = [
        "printenv",
        # at the beginning make copy of the repo_dir to avoid conflicts with other jobs
        "cp -r /temp_repo/. /workspace/",
        "ls -lah",
        f"cat {docker_run_script}",
        f"bash {docker_run_script}"
    ]
    # wrap each one (capturing stdout+stderr) and join with &&
    docker_run_cmd = " && ".join(
        f"{cmd} | boxes -d plain" for cmd in run_cmds
    )
    job_cmd = (
        "docker run --rm"
        f" -v {repo_dir}:/temp_repo"
        " -w /workspace"
        f" {docker_run_env} {docker_run_image}"
        f" bash -lc 'apt-get update && apt-get install -y --no-install-recommends boxes && {docker_run_cmd}'"
    )
    logging.debug(f"job >> {job_cmd}")
    job = Job.run(
        name=job_name,
        command=job_cmd,
        machine=docker_run_machine,
        interruptible=config.get("interruptible", False),
    )
    await job.async_wait()  # wait for the job to finish

    success = job.status == Status.Completed
    # todo: cleanup job if needed or success
    return success, f"run finished as {job.status}\n{job.logs}"
